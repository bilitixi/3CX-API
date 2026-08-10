from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import ThreeCXAuthenticationError, ThreeCXServiceUnavailableError
from app.core.logging import logger
from app.services.token_manager import ThreeCXTokenManager, token_manager as default_token_manager

MAX_RETRIES = 1


def _normalize_entity(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """3CX's documented callcontrol schema (GET /callcontrol/{dn}, GET
    /callcontrol/{dn}/participants/{id}) uses lowercase snake_case field names —
    id, status, callid, dn, participants, plus devices/type at the DN level that
    this codebase doesn't use. The rest of this codebase was written against
    PascalCase (Id, Status, CallId, Participants) — re-expose the documented
    fields under those keys too (without discarding the originals) so existing
    call sites keep working regardless of which casing 3CX actually sends.
    """
    if not data:
        return data

    normalized = dict(data)
    normalized.setdefault("Id", normalized.get("id"))
    normalized.setdefault("Status", normalized.get("status"))
    normalized.setdefault("Dn", normalized.get("dn"))
    if "CallId" not in normalized:
        normalized["CallId"] = normalized.get("callid", normalized.get("Callid"))

    participants = normalized.get("Participants", normalized.get("participants"))
    if participants is not None:
        normalized["Participants"] = [_normalize_entity(p) for p in participants]

    return normalized


class ThreeCXClient:
    """Thin REST wrapper over the 3CX Call Control API.

    Every request fetches a token via the shared `ThreeCXTokenManager` rather than
    holding one directly, and retries once on a 401 (treated as "token invalid/expired").
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token_manager: Optional[ThreeCXTokenManager] = None,
    ):
        self.base_url = (base_url or settings.threecx_pbx_base_url).rstrip("/")
        self.token_manager = token_manager or default_token_manager

    async def _request(self, method: str, path: str, force_refresh: bool = False, **kwargs: Any) -> Any:
        token = await self.token_manager.get_valid_token(force_refresh=force_refresh)
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        except httpx.RequestError as exc:
            raise ThreeCXServiceUnavailableError(f"3CX request failed: {exc}") from exc

        if response.status_code == 401 and not force_refresh:
            logger.warning("threecx_401_retrying_with_fresh_token", path=path)
            return await self._request(method, path, force_refresh=True, headers=headers, **kwargs)
        if response.status_code == 401:
            raise ThreeCXAuthenticationError(f"3CX rejected refreshed token for {path}")
        if response.status_code >= 500:
            raise ThreeCXServiceUnavailableError(f"3CX server error {response.status_code} on {path}")

        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    async def make_call(
        self,
        dn: str,
        destination: str,
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        """Initiate a call from `dn` to `destination`.

        3CX's documented response envelope wraps the actual call/participant data
        under `result` (alongside `finalstatus`/`reason`/`reasontext` describing the
        makecall request itself, not the call) — it is NOT a flat participant object:
        `{"finalstatus": ..., "reason": ..., "reasontext": ..., "result": {"callid": ..., "id": ..., "status": ..., ...}}`.
        This returns that inner `result`, normalized like `get_entity`.
        """
        response = await self._request(
            "POST",
            f"/callcontrol/{dn}/makecall",
            json={"destination": destination, "timeout": timeout_ms},
        )
        response = response or {}
        logger.info(
            "threecx_makecall_response",
            dn=dn,
            destination=destination,
            finalstatus=response.get("finalstatus"),
            reason=response.get("reason"),
            reasontext=response.get("reasontext"),
        )
        return _normalize_entity(response.get("result")) or {}

    async def get_entity(self, path: str) -> Optional[Dict[str, Any]]:
        """Fetch the current state of a callcontrol entity (e.g. a participant).

        3CX's WebSocket push only announces that an entity at `path` changed
        (event_type + entity path) without the entity's data, so callers must
        GET it to see the actual status.
        """
        return _normalize_entity(await self._request("GET", path))

    async def get_participants(self, dn: str) -> list:
        """Return the current participant list for a DN (extension or queue)."""
        entity = await self.get_entity(f"/callcontrol/{dn}")
        return (entity or {}).get("Participants", [])

    async def drop_participant(self, dn: str, participant_id: Any) -> None:
        """Hang up a single participant, e.g. to stop the other still-ringing
        queue members once one of them has already answered.
        """
        await self._request("POST", f"/callcontrol/{dn}/participants/{participant_id}/drop")
