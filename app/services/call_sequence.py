import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.services.client import ThreeCXClient

# How often to poll a ringing participant's status while waiting for an answer.
POLL_INTERVAL_SECONDS = 5

# 3CX status strings observed for a bridged/answered participant.
CONNECTED_STATUSES = {"Connected", "Talking"}

# How long a finished sequence (answered/exhausted/cancelled/error) is kept around
# for GET /sequence/{id} lookups before the background sweep prunes it, so a
# long-running process doesn't accumulate one entry per call forever.
PRUNE_FINISHED_AFTER_SECONDS = 3600

# How often the background sweep task checks for stale finished sequences to prune.
SWEEP_INTERVAL_SECONDS = 300


@dataclass
class SequenceState:
    id: str
    dns: List[str]
    queue_dn: str
    interval_seconds: float
    status: str = "running"  # running | answered | exhausted | cancelled | error
    current_dn: Optional[str] = None
    current_index: int = -1
    answered_dn: Optional[str] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    # The still-ringing participant this sequence is currently responsible for
    # (so it can be dropped on cancel or before moving to the next dn).
    current_participant: Optional[Dict[str, Any]] = None
    # time.monotonic() timestamp of when _run() finished, for pruning. None while running.
    finished_at: Optional[float] = None


class CallSequenceManager:
    """Rings a list of DNs into a queue one at a time, waiting `interval_seconds`
    between each attempt, and stopping as soon as one answers. The still-ringing
    call is dropped before moving on to the next dn (or on cancel), so only one
    call in the sequence is ever active at a time.
    """

    def __init__(self, client: Optional[ThreeCXClient] = None, sweep_interval_seconds: float = SWEEP_INTERVAL_SECONDS):
        self.client = client or ThreeCXClient()
        self._sequences: Dict[str, SequenceState] = {}
        self._latest_sequence_id: Optional[str] = None
        self._sweep_interval_seconds = sweep_interval_seconds
        self._sweep_task: Optional[asyncio.Task] = None

    def start(self, dns: List[str], queue_dn: str, interval_seconds: float) -> SequenceState:
        sequence_id = uuid.uuid4().hex
        state = SequenceState(id=sequence_id, dns=dns, queue_dn=queue_dn, interval_seconds=interval_seconds)
        self._sequences[sequence_id] = state
        self._latest_sequence_id = sequence_id
        state.task = asyncio.create_task(self._run(state))
        return state

    def start_sweeper(self) -> None:
        """Start the background task that periodically prunes finished sequences.
        Call once at app startup; safe to call more than once (no-op if already running).
        """
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_interval_seconds)
            self._prune_finished()

    def _prune_finished(self) -> None:
        """Drop sequences that finished more than PRUNE_FINISHED_AFTER_SECONDS ago."""
        cutoff = time.monotonic() - PRUNE_FINISHED_AFTER_SECONDS
        stale_ids = [
            sequence_id
            for sequence_id, state in self._sequences.items()
            if state.finished_at is not None and state.finished_at < cutoff
        ]
        for sequence_id in stale_ids:
            del self._sequences[sequence_id]
            logger.info("call_sequence_pruned", sequence_id=sequence_id)

    def get(self, sequence_id: str) -> Optional[SequenceState]:
        return self._sequences.get(sequence_id)

    async def cancel(self, sequence_id: str) -> Optional[SequenceState]:
        state = self._sequences.get(sequence_id)
        if state is None:
            return None
        if state.status == "running":
            state.cancel_event.set()
        return state

    async def cancel_latest(self) -> Optional[SequenceState]:
        """Cancel whichever sequence was started most recently (by any caller).
        Returns None if no sequence has ever been started.
        """
        if self._latest_sequence_id is None:
            return None
        return await self.cancel(self._latest_sequence_id)

    def shutdown(self) -> None:
        """Cancel every in-flight sequence's background task and the sweeper, e.g.
        on app shutdown.
        """
        for state in self._sequences.values():
            if state.task is not None:
                state.task.cancel()
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            self._sweep_task = None

    async def _run(self, state: SequenceState) -> None:
        try:
            for index, dn in enumerate(state.dns):
                if state.cancel_event.is_set():
                    state.status = "cancelled"
                    return

                state.current_index = index
                state.current_dn = dn
                logger.info("call_sequence_dialing", sequence_id=state.id, dn=dn, index=index)

                try:
                    result = await self.client.make_call(dn, state.queue_dn)
                except Exception as exc:
                    logger.warning(
                        "call_sequence_makecall_failed", sequence_id=state.id, dn=dn, error=str(exc)
                    )
                    state.current_participant = None
                    if await self._wait_or_cancel(state, state.interval_seconds):
                        state.status = "cancelled"
                        return
                    continue

                participant_id = (result or {}).get("Id")
                state.current_participant = (
                    {"dn": dn, "id": participant_id} if participant_id is not None else None
                )

                answered = await self._wait_for_answer_or_timeout(state, dn, participant_id)

                if state.cancel_event.is_set():
                    await self._drop_current(state)
                    state.status = "cancelled"
                    return

                if answered:
                    logger.info("call_sequence_answered", sequence_id=state.id, dn=dn)
                    state.status = "answered"
                    state.answered_dn = dn
                    state.current_participant = None
                    return

                # Timed out waiting for an answer — drop this leg and try the next dn.
                await self._drop_current(state)

            state.status = "exhausted"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("call_sequence_failed", sequence_id=state.id, error=str(exc))
            state.status = "error"
        finally:
            # Runs on every exit path — answered/exhausted/cancelled/error, and even
            # when the task itself is cancelled (e.g. manager.shutdown()) — so pruning
            # can always tell how long ago this sequence stopped doing anything.
            state.finished_at = time.monotonic()

    async def _wait_for_answer_or_timeout(self, state: SequenceState, dn: str, participant_id) -> bool:
        """Poll the participant's status until it's answered, the interval elapses,
        or the sequence is cancelled. Returns True if answered.
        """
        elapsed = 0.0
        while elapsed < state.interval_seconds:
            if state.cancel_event.is_set():
                return False

            if participant_id is not None:
                try:
                    entity = await self.client.get_entity(f"/callcontrol/{dn}/participants/{participant_id}")
                except Exception as exc:
                    logger.warning(
                        "call_sequence_status_check_failed", sequence_id=state.id, dn=dn, error=str(exc)
                    )
                    entity = None
                if entity and entity.get("Status") in CONNECTED_STATUSES:
                    return True

            wait_for = min(POLL_INTERVAL_SECONDS, state.interval_seconds - elapsed)
            if await self._wait_or_cancel(state, wait_for):
                return False
            elapsed += wait_for

        return False

    async def _wait_or_cancel(self, state: SequenceState, seconds: float) -> bool:
        """Sleep up to `seconds`, waking early if cancelled. Returns True if cancelled."""
        try:
            await asyncio.wait_for(state.cancel_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _drop_current(self, state: SequenceState) -> None:
        participant = state.current_participant
        state.current_participant = None
        if not participant or participant.get("id") is None:
            return
        try:
            await self.client.drop_participant(participant["dn"], participant["id"])
            logger.info(
                "call_sequence_dropped",
                sequence_id=state.id,
                dn=participant["dn"],
                participant_id=participant["id"],
            )
        except Exception as exc:
            logger.warning(
                "call_sequence_drop_failed",
                sequence_id=state.id,
                dn=participant["dn"],
                error=str(exc),
            )


call_sequence_manager = CallSequenceManager()
