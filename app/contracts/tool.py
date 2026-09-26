from typing import Any
from uuid import UUID

from pydantic import Field

from app.contracts.common import ContractModel, EvidenceReference, RunStatus


class ToolInput(ContractModel):
    run_id: UUID
    case_id: UUID | None = None
    correlation_id: str | None = None


class ToolOutput(ContractModel):
    evidence: list[EvidenceReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ToolError(ContractModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ToolExecution[InputT, OutputT](ContractModel):
    run_id: UUID
    tool_name: str
    status: RunStatus
    input: InputT
    output: OutputT | None = None
    latency_ms: int = Field(ge=0)
    error: ToolError | None = None
