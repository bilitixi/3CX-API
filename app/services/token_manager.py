import asyncio
import time
from typing import Optional

import httpx

from app.core.config import settings
from app.core.exceptions import ThreeCXAuthenticationError, ThreeCXNotConfiguredError
from app.core.logging import logger


class ThreeCXTokenManager:
    """Caches the 3CX Call Control API bearer token and refreshes it before it expires.

    A single instance is shared app-wide (see `token_manager` below) so every REST call
    and WebSocket (re)connection goes through the same refresh logic.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        grant_type: Optional[str] = None,
        safety_margin_seconds: Optional[int] = None,
    ):
        self.base_url = (base_url or settings.threecx_pbx_base_url).rstrip("/")
        self.client_id = client_id or settings.threecx_client_id
        self.client_secret = client_secret or settings.threecx_client_secret
        self.grant_type = grant_type or settings.threecx_grant_type
        self.safety_margin_seconds = (
            safety_margin_seconds
            if safety_margin_seconds is not None
            else settings.threecx_token_safety_margin_seconds
        )

        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._on_refresh: list = []

    def is_configured(self) -> bool:
        return bool(self.base_url and self.client_id and self.client_secret)

    def on_refresh(self, callback) -> None:
        """Register a callback invoked (with the new token) whenever the token is refreshed.

        Used to trigger a WebSocket reconnect, since 3CX ties WS auth to the token used
        at connect time.
        """
        self._on_refresh.append(callback)

    def _is_expired(self) -> bool:
        return self._token is None or time.monotonic() >= self._expires_at

    async def get_valid_token(self, force_refresh: bool = False) -> str:
        if not self.is_configured():
            raise ThreeCXNotConfiguredError(
                "3CX Call Control API is not configured "
                "(THREECX_PBX_BASE_URL / THREECX_CLIENT_ID / THREECX_CLIENT_SECRET)"
            )

        async with self._lock:
            if force_refresh or self._is_expired():
                await self._refresh()
            return self._token  # type: ignore[return-value]

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/connect/token",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": self.grant_type,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.RequestError as exc:
                raise ThreeCXAuthenticationError(f"Failed to reach 3CX token endpoint: {exc}") from exc

        if response.status_code != 200:
            raise ThreeCXAuthenticationError(
                f"3CX token request failed: {response.status_code} {response.text}"
            )

        body = response.json()
        self._token = body["access_token"]
        expires_in = body.get("expires_in", 3600)
        self._expires_at = time.monotonic() + expires_in - self.safety_margin_seconds
        logger.info("threecx_token_refreshed", expires_in=expires_in)

        for callback in self._on_refresh:
            callback(self._token)


token_manager = ThreeCXTokenManager()
