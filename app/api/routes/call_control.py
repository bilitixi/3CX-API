from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.core.exceptions import ThreeCXNotConfiguredError
from app.schemas.call_control import (
    DialIntoQueueRequest,
    DialIntoQueueResponse,
)
from app.services.client import ThreeCXClient
from app.services.escalation import queue_answer_watcher
from app.services.token_manager import token_manager

router = APIRouter(prefix="/calls", tags=["3cx-call-control"])


@router.post("/dial-into-queue", response_model=DialIntoQueueResponse)
async def dial_into_queue(payload: DialIntoQueueRequest = DialIntoQueueRequest()) -> DialIntoQueueResponse:
    """Makes source_dn call queue_dn: source_dn's phone rings first, and once answered
    is connected into the queue, which then rings whichever agents are logged into it
    per its configured ring strategy. source_dn defaults to THREECX_SOURCE_DN and
    queue_dn to THREECX_QUEUE_DN from config, so this can be called with no request
    body at all.
    """
    if not token_manager.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="3CX Call Control API is not configured",
        )

    source_dn = payload.source_dn or settings.threecx_source_dn
    if not source_dn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_dn was not provided and THREECX_SOURCE_DN is not set",
        )

    queue_dn = payload.queue_dn or settings.threecx_queue_dn
    if not queue_dn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="queue_dn was not provided and THREECX_QUEUE_DN is not set",
        )

    try:
        result = await ThreeCXClient().make_call(source_dn, queue_dn)
    except ThreeCXNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    call_id = (result or {}).get("CallId", (result or {}).get("Callid"))
    queue_answer_watcher.watch(call_id)

    return DialIntoQueueResponse(source_dn=source_dn, queue_dn=queue_dn)
