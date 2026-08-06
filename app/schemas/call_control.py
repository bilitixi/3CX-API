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
