"""Request and response contracts for agent investigations."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from agent.factory.model import MAX_PROMPT_BYTES
from backend.auth.schemas import Namespace


class InvestigateRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_PROMPT_BYTES)]
    namespace: Namespace | None = None


class InvestigationMetadata(BaseModel):
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    tools_used: list[str]
    tool_call_count: int = Field(ge=0)


class InvestigateResponse(BaseModel):
    investigation_id: UUID
    status: Literal["completed", "failed"]
    answer: str
    metadata: InvestigationMetadata
