import asyncio
import time

import pytest

from app.services.call_sequence import PRUNE_FINISHED_AFTER_SECONDS, CallSequenceManager

# Keep test intervals well under the manager's poll interval so timeouts and
# cancellation actually get exercised quickly instead of waiting out a real poll tick.
FAST_INTERVAL = 0.05


class FakeClient:
    def __init__(self, statuses=None, fail_makecall_for=None):
        # statuses: dict[dn] -> status string to report for that dn's participant.
        # Any dn not listed stays "Ringing" (never answers) for the whole test.
        self.statuses = statuses or {}
        self.fail_makecall_for = fail_makecall_for or set()
        self.make_call_calls = []
        self.dropped = []
        self._next_id = 1

    async def make_call(self, dn, destination):
        self.make_call_calls.append((dn, destination))
        if dn in self.fail_makecall_for:
            raise RuntimeError("3CX rejected the call")
        participant_id = self._next_id
        self._next_id += 1
        return {"Id": participant_id, "CallId": participant_id}

    async def get_entity(self, path):
        # path looks like /callcontrol/{dn}/participants/{id}
        dn = path.split("/")[2]
        return {"Status": self.statuses.get(dn, "Ringing")}

    async def drop_participant(self, dn, participant_id):
        self.dropped.append((dn, participant_id))


async def _wait_until_finished(state, timeout=2.0):
    try:
        await asyncio.wait_for(state.task, timeout=timeout)
    except asyncio.TimeoutError:
        pytest.fail(f"sequence {state.id} did not finish within {timeout}s (status={state.status})")


@pytest.mark.asyncio
async def test_sequence_stops_as_soon_as_a_dn_answers():
    client = FakeClient(statuses={"1005": "Connected"})
    manager = CallSequenceManager(client=client)

    state = manager.start(["1003", "1005", "1006"], queue_dn="8003", interval_seconds=FAST_INTERVAL)
    await _wait_until_finished(state)

    assert state.status == "answered"
    assert state.answered_dn == "1005"
    # Should not have gone on to dial 1006 after 1005 answered.
    assert [dn for dn, _ in client.make_call_calls] == ["1003", "1005"]
    # The one that timed out (1003) should have been dropped before moving on.
    assert client.dropped == [("1003", 1)]


@pytest.mark.asyncio
async def test_sequence_exhausts_list_when_nobody_answers():
    client = FakeClient()  # nobody ever answers
    manager = CallSequenceManager(client=client)

    state = manager.start(["1003", "1005"], queue_dn="8003", interval_seconds=FAST_INTERVAL)
    await _wait_until_finished(state)

    assert state.status == "exhausted"
    assert [dn for dn, _ in client.make_call_calls] == ["1003", "1005"]
    assert client.dropped == [("1003", 1), ("1005", 2)]


@pytest.mark.asyncio
async def test_cancel_stops_the_sequence_and_drops_the_current_call():
    client = FakeClient()  # nobody answers, so it'll sit waiting until cancelled
    manager = CallSequenceManager(client=client)

    state = manager.start(["1003", "1005", "1006"], queue_dn="8003", interval_seconds=10)
    # Give the loop a tick to place the first call and start waiting.
    await asyncio.sleep(0.05)

    result = await manager.cancel(state.id)
    await _wait_until_finished(state)

    assert result is state
    assert state.status == "cancelled"
    assert [dn for dn, _ in client.make_call_calls] == ["1003"]
    assert client.dropped == [("1003", 1)]


@pytest.mark.asyncio
async def test_cancel_unknown_sequence_returns_none():
    manager = CallSequenceManager(client=FakeClient())

    result = await manager.cancel("does-not-exist")

    assert result is None


@pytest.mark.asyncio
async def test_cancel_latest_returns_none_when_nothing_ever_started():
    manager = CallSequenceManager(client=FakeClient())

    result = await manager.cancel_latest()

    assert result is None


@pytest.mark.asyncio
async def test_cancel_latest_targets_the_most_recently_started_sequence():
    client = FakeClient()  # nobody answers, both sequences sit waiting
    manager = CallSequenceManager(client=client)

    first = manager.start(["1003"], queue_dn="8003", interval_seconds=10)
    await asyncio.sleep(0.02)
    second = manager.start(["1005"], queue_dn="8003", interval_seconds=10)
    await asyncio.sleep(0.02)

    result = await manager.cancel_latest()
    await _wait_until_finished(second)

    assert result is second
    assert second.status == "cancelled"
    # The first sequence should be untouched — still running.
    assert first.status == "running"

    # Clean up the still-running first sequence so the test doesn't leak a task.
    await manager.cancel(first.id)
    await _wait_until_finished(first)


@pytest.mark.asyncio
async def test_makecall_failure_is_logged_and_sequence_continues():
    client = FakeClient(fail_makecall_for={"1003"}, statuses={"1005": "Connected"})
    manager = CallSequenceManager(client=client)

    state = manager.start(["1003", "1005"], queue_dn="8003", interval_seconds=FAST_INTERVAL)
    await _wait_until_finished(state)

    assert state.status == "answered"
    assert state.answered_dn == "1005"
    assert client.dropped == []  # 1003 never got a participant id to drop


@pytest.mark.asyncio
async def test_finished_at_is_set_once_the_sequence_stops():
    client = FakeClient(statuses={"1003": "Connected"})
    manager = CallSequenceManager(client=client)

    state = manager.start(["1003"], queue_dn="8003", interval_seconds=FAST_INTERVAL)
    assert state.finished_at is None  # not set while still running

    await _wait_until_finished(state)

    assert state.finished_at is not None


@pytest.mark.asyncio
async def test_prune_finished_removes_sequences_finished_long_ago():
    client = FakeClient()
    manager = CallSequenceManager(client=client)

    old = manager.start(["1003"], queue_dn="8003", interval_seconds=FAST_INTERVAL)
    await _wait_until_finished(old)
    # Backdate it as if it finished well past the prune window.
    old.finished_at = time.monotonic() - PRUNE_FINISHED_AFTER_SECONDS - 1

    manager._prune_finished()

    assert manager.get(old.id) is None


@pytest.mark.asyncio
async def test_prune_finished_keeps_recently_finished_sequences():
    client = FakeClient()
    manager = CallSequenceManager(client=client)

    old = manager.start(["1003"], queue_dn="8003", interval_seconds=FAST_INTERVAL)
    await _wait_until_finished(old)  # finished_at is "just now", well inside the window

    manager._prune_finished()

    assert manager.get(old.id) is old


@pytest.mark.asyncio
async def test_prune_finished_keeps_still_running_sequences():
    client = FakeClient()  # nobody answers, so it'll sit waiting
    manager = CallSequenceManager(client=client)

    running = manager.start(["1003"], queue_dn="8003", interval_seconds=10)
    await asyncio.sleep(0.05)  # let it start dialing

    manager._prune_finished()

    assert manager.get(running.id) is running  # finished_at is None, never eligible for pruning

    await manager.cancel(running.id)
    await _wait_until_finished(running)


@pytest.mark.asyncio
async def test_sweep_loop_prunes_on_a_timer():
    client = FakeClient()
    manager = CallSequenceManager(client=client, sweep_interval_seconds=FAST_INTERVAL)

    old = manager.start(["1003"], queue_dn="8003", interval_seconds=FAST_INTERVAL)
    await _wait_until_finished(old)
    old.finished_at = time.monotonic() - PRUNE_FINISHED_AFTER_SECONDS - 1

    manager.start_sweeper()
    try:
        # Give the sweep loop a couple of its (fast) intervals to fire.
        await asyncio.sleep(FAST_INTERVAL * 3)
        assert manager.get(old.id) is None
    finally:
        manager.shutdown()


@pytest.mark.asyncio
async def test_start_sweeper_is_idempotent():
    manager = CallSequenceManager(client=FakeClient(), sweep_interval_seconds=FAST_INTERVAL)

    manager.start_sweeper()
    first_task = manager._sweep_task
    manager.start_sweeper()

    assert manager._sweep_task is first_task  # second call was a no-op

    manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_the_sweep_task():
    manager = CallSequenceManager(client=FakeClient(), sweep_interval_seconds=FAST_INTERVAL)
    manager.start_sweeper()

    manager.shutdown()
    await asyncio.sleep(0)  # let the cancellation propagate

    assert manager._sweep_task is None
