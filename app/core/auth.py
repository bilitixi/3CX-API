from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    """Require `Authorization: Bearer <API_AUTH_TOKEN>` on every request.

    If API_AUTH_TOKEN is unset (e.g. local development), auth is skipped entirely.
    """
    expected = settings.api_auth_token
    if not expected:
        return

    if credentials is None or credentials.credentials != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
