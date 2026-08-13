from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.schemas.call_control import CallSequenceStatusResponse, StartCallSequenceRequest
from app.services.call_sequence import SequenceState, call_sequence_manager
from app.services.token_manager import token_manager

router = APIRouter(prefix="/calls", tags=["3cx-call-control"])


def _sequence_to_response(state: SequenceState) -> CallSequenceStatusResponse:
    return CallSequenceStatusResponse(
        sequence_id=state.id,
        status=state.status,
        dns=state.dns,
        queue_dn=state.queue_dn,
        interval_seconds=state.interval_seconds,
        current_index=state.current_index,
        current_dn=state.current_dn,
        answered_dn=state.answered_dn,
    )


@router.post("/sequence/start", response_model=CallSequenceStatusResponse)
async def start_call_sequence(
    payload: StartCallSequenceRequest = StartCallSequenceRequest(),
) -> CallSequenceStatusResponse:
    """Ring each dn in `dns`, one at a time, into `queue_dn`. Waits `interval_seconds`
    for an answer before dropping that call and trying the next dn. Stops as soon as
    one answers. Only one call is ever ringing at a time — the previous one is
    dropped before the next starts. `dns` and `queue_dn` both fall back to config
    (THREECX_SEQUENCE_DNS, THREECX_QUEUE_DN) when omitted, so this can be called
    with an empty body. Returns immediately with the sequence's id and initial
    status; poll GET /calls/sequence/{sequence_id} for progress, or
    POST /calls/sequence/{sequence_id}/cancel (or /calls/sequence/cancel) to stop
    it early.
    """
    if not token_manager.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="3CX Call Control API is not configured",
        )

    dns = payload.dns or settings.sequence_dns_list
    if not dns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="dns was not provided and THREECX_SEQUENCE_DNS is not set",
        )

    queue_dn = payload.queue_dn or settings.threecx_queue_dn
    if not queue_dn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="queue_dn was not provided and THREECX_QUEUE_DN is not set",
        )

    interval_seconds = payload.interval_seconds or settings.threecx_sequence_interval_seconds

    state = call_sequence_manager.start(dns, queue_dn, interval_seconds)
    return _sequence_to_response(state)


@router.post("/sequence/cancel", response_model=CallSequenceStatusResponse)
async def cancel_latest_call_sequence() -> CallSequenceStatusResponse:
    """Cancel whichever call sequence was started most recently, without needing
    its sequence_id. Convenience for callers that only ever run one sequence at
    a time; use POST /calls/sequence/{sequence_id}/cancel to target a specific
    one if more than one might be running.
    """
    state = await call_sequence_manager.cancel_latest()
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No call sequence has been started yet"
        )
    return _sequence_to_response(state)


@router.get("/sequence/{sequence_id}", response_model=CallSequenceStatusResponse)
async def get_call_sequence(sequence_id: str) -> CallSequenceStatusResponse:
    """Check a call sequence's current status/progress."""
    state = call_sequence_manager.get(sequence_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown sequence_id")
    return _sequence_to_response(state)


@router.post("/sequence/{sequence_id}/cancel", response_model=CallSequenceStatusResponse)
async def cancel_call_sequence(sequence_id: str) -> CallSequenceStatusResponse:
    """Stop a call sequence at any point, dropping whichever dn is currently ringing."""
    state = await call_sequence_manager.cancel(sequence_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown sequence_id")
    return _sequence_to_response(state)
