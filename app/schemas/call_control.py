from typing import Any, List, Optional

from pydantic import BaseModel, Field


class DialIntoQueueRequest(BaseModel):
    source_dn: Optional[str] = Field(
        None,
        description="Internal extension/number to ring first, e.g. '0800111222'. Defaults to THREECX_SOURCE_DN from config if omitted.",
    )
    queue_dn: Optional[str] = Field(
        None,
        description="Queue to connect source_dn into once it answers. Defaults to THREECX_QUEUE_DN from config.",
    )


class DialIntoQueueResponse(BaseModel):
    source_dn: str
    queue_dn: str
    call_id: Optional[Any] = Field(
        None,
        description="3CX call id for this call, as returned by makecall. Pass to "
        "GET /calls/{call_id}/dtmf to retrieve any digits keyed in on it.",
    )


class CapturedDtmfResponse(BaseModel):
    call_id: Any
    digits: List[str] = Field(
        default_factory=list,
        description="DTMF digit-strings 3CX reported for this call, in the order received.",
    )


class StartCallSequenceRequest(BaseModel):
    dns: Optional[List[str]] = Field(
        None,
        description="Ordered list of DNs to try, one at a time, e.g. ['1003', '1005', '1006']. "
        "Defaults to THREECX_SEQUENCE_DNS from config if omitted.",
    )
    queue_dn: Optional[str] = Field(
        None,
        description="Queue each DN gets bridged into once it answers. Defaults to THREECX_QUEUE_DN.",
    )
    interval_seconds: float = Field(
        120,
        gt=0,
        description="Seconds to wait for an answer before cancelling that call and trying the next dn.",
    )


class CallSequenceStatusResponse(BaseModel):
    sequence_id: str
    status: str = Field(
        description="running | answered | exhausted | cancelled | error"
    )
    dns: List[str]
    queue_dn: str
    interval_seconds: float
    current_index: int = Field(description="Index into dns of the call currently (or last) in flight, -1 if not started.")
    current_dn: Optional[str] = None
    answered_dn: Optional[str] = Field(
        None, description="Which dn answered, once status is 'answered'."
    )
