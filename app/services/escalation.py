import asyncio
import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

import httpx
import websockets

from app.core.logging import logger
from app.services.client import ThreeCXClient
from app.services.token_manager import ThreeCXTokenManager, token_manager as default_token_manager

# 3CX status strings observed for a bridged/answered participant.
CONNECTED_STATUSES = {"Connected", "Talking"}

RECONNECT_DELAY_SECONDS = 5


class QueueAnswerWatcher:
    """Watches 3CX callcontrol WebSocket events and drops the other still-ringing
    participants of a call as soon as one of them answers. Also captures any DTMF
    digits 3CX reports a participant as having entered, keyed by call id.

    Every event 3CX pushes over this WebSocket is wrapped as
    `{"sequence": ..., "event": {"event_type": ..., "entity": ..., "attached_data": {...}}}`.
    `entity` names the changed participant (e.g. "/callcontrol/1004/participants/7").
    `attached_data.dtmf_input`, when present, holds digits 3CX detected that participant
    entering, and is read directly off the event — no GET needed. A GET on `entity` is
    still made to read status/call id (e.g. to know when to drop other participants),
    but the participant may already be gone by the time that GET runs (e.g. it just
    hung up), which 3CX reports as 403 rather than 404; that's treated as non-fatal and
    doesn't cost us DTMF already read from the event, nor tear down the WebSocket.
    Reconnects whenever the shared token manager refreshes, since 3CX ties WS auth to
    the token used at connect time.
    """

    def __init__(
        self,
        client: Optional[ThreeCXClient] = None,
        token_manager: Optional[ThreeCXTokenManager] = None,
    ):
        self.client = client or ThreeCXClient()
        self.token_manager = token_manager or default_token_manager
        self._task: Optional[asyncio.Task] = None
        self._watched_call_ids: Set[Any] = set()
        self._captured_dtmf: Dict[Any, List[str]] = defaultdict(list)
        self.token_manager.on_refresh(lambda _token: self._reconnect())

    def watch(self, call_id) -> None:
        """Start reacting to an answer event for this call id (returned by make_call)."""
        if call_id is not None:
            self._watched_call_ids.add(call_id)

    def get_captured_dtmf(self, call_id) -> List[str]:
        """Return the DTMF digit-strings reported for this call id so far, in order."""
        return list(self._captured_dtmf.get(call_id, []))

    def start(self) -> None:
        if self.token_manager.is_configured() and self._task is None:
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _reconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("threecx_ws_reconnecting", error=str(exc))
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _listen_once(self) -> None:
        token = await self.token_manager.get_valid_token()
        ws_url = f"{self.client.base_url}/callcontrol/ws".replace("https://", "wss://").replace(
            "http://", "ws://"
        )
        async with websockets.connect(ws_url, extra_headers={"Authorization": f"Bearer {token}"}) as ws:
            logger.info("threecx_ws_connected")
            async for message in ws:
                await self._handle_message(message)

    async def _handle_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except (TypeError, ValueError):
            return

        event = payload.get("event") or {}
        entity_path = event.get("entity")
        if not entity_path or "/participants/" not in entity_path:
            return

        dn = entity_path.split("/")[2]

        # attached_data (and any dtmf_input on it) is already in the message we have —
        # no GET needed to read it. Grab it before the GET below, so a participant
        # that's already gone by the time we look it up (e.g. it just hung up, which
        # 3CX reports as 403 rather than 404) doesn't cost us digits it already sent.
        attached_data = event.get("attached_data") or {}
        dtmf_input = attached_data.get("dtmf_input")

        try:
            entity = await self.client.get_entity(entity_path)
        except httpx.HTTPStatusError as exc:
            logger.info(
                "threecx_participant_lookup_failed",
                entity=entity_path,
                status_code=exc.response.status_code,
            )
            entity = None

        call_id = (entity or {}).get("CallId", (entity or {}).get("Callid"))

        if dtmf_input:
            # Fall back to the raw entity path as the capture key when the participant
            # is already gone and we couldn't resolve its call id via GET.
            capture_key = call_id if call_id is not None else entity_path
            logger.info("threecx_dtmf_received", dn=dn, call_id=capture_key, dtmf=dtmf_input)
            self._captured_dtmf[capture_key].append(dtmf_input)

        if not entity or not self._watched_call_ids or call_id not in self._watched_call_ids:
            return

        status = entity.get("Status")
        if status not in CONNECTED_STATUSES:
            return

        await self._drop_other_participants(dn, call_id, connected_id=entity.get("Id"))
        self._watched_call_ids.discard(call_id)

    async def _drop_other_participants(self, dn: str, call_id, connected_id) -> None:
        try:
            participants = await self.client.get_participants(dn)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "threecx_get_participants_failed", dn=dn, status_code=exc.response.status_code
            )
            return

        for participant in participants:
            if participant.get("Id") == connected_id:
                continue
            if participant.get("CallId", participant.get("Callid")) != call_id:
                continue
            if participant.get("Status") in CONNECTED_STATUSES:
                continue
            try:
                await self.client.drop_participant(dn, participant["Id"])
                logger.info("threecx_dropped_other_participant", dn=dn, participant_id=participant["Id"])
            except Exception as exc:
                logger.warning("threecx_drop_participant_failed", dn=dn, error=str(exc))


queue_answer_watcher = QueueAnswerWatcher()
