from typing import Optional

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
