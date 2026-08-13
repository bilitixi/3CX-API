from typing import List, Optional

from pydantic import BaseModel, Field


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
    interval_seconds: Optional[float] = Field(
        None,
        gt=0,
        description="Seconds to wait for an answer before cancelling that call and trying the next dn. "
        "Defaults to THREECX_SEQUENCE_INTERVAL_SECONDS from config if omitted.",
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
