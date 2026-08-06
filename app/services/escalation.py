import asyncio
import json
from typing import Any, Optional, Set

import websockets

from app.core.logging import logger
from app.services.client import ThreeCXClient
from app.services.token_manager import ThreeCXTokenManager, token_manager as default_token_manager

# 3CX status strings observed for a bridged/answered participant.
CONNECTED_STATUSES = {"Connected", "Talking"}

RECONNECT_DELAY_SECONDS = 5


class QueueAnswerWatcher:
    """Watches 3CX callcontrol WebSocket events and drops the other still-ringing
    participants of a call as soon as one of them answers.

    3CX's WebSocket only pushes "this entity changed" (no payload), so each event
    is followed by a GET to read the participant's actual status. Reconnects
    whenever the shared token manager refreshes, since 3CX ties WS auth to the
    token used at connect time.
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
        self.token_manager.on_refresh(lambda _token: self._reconnect())

    def watch(self, call_id) -> None:
        """Start reacting to an answer event for this call id (returned by make_call)."""
        if call_id is not None:
            self._watched_call_ids.add(call_id)

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
        if not self._watched_call_ids:
            return
        try:
            payload = json.loads(message)
        except (TypeError, ValueError):
            return

        entity_path = payload.get("entity")
        if not entity_path or "/participants/" not in entity_path:
            return

        dn = entity_path.split("/")[2]
        entity = await self.client.get_entity(entity_path)
        if not entity:
            return

        status = entity.get("Status")
        call_id = entity.get("CallId", entity.get("Callid"))
        if status not in CONNECTED_STATUSES or call_id not in self._watched_call_ids:
            return

        await self._drop_other_participants(dn, call_id, connected_id=entity.get("Id"))
        self._watched_call_ids.discard(call_id)

    async def _drop_other_participants(self, dn: str, call_id, connected_id) -> None:
        participants = await self.client.get_participants(dn)
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
