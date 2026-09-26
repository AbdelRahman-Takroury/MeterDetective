from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from app.contracts.common import ContractModel, EvidenceReference, RunStatus, utc_now


class EventType(StrEnum):
    READING_RECEIVED = "reading.received"
    ANOMALY_DETECTED = "anomaly.detected"
    INVESTIGATION_REQUESTED = "investigation.requested"
    APPROVAL_DECIDED = "approval.decided"
    ACTION_COMPLETED = "action.completed"
    VERIFICATION_REQUESTED = "verification.requested"


class EventPayload(ContractModel):
    event_id: UUID
    event_type: EventType
    occurred_at: datetime
    meter_ids: list[str] = Field(default_factory=list)
    transformer_id: str | None = None
    source: str
    schema_version: str = "1.0"
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_scope(self) -> "EventPayload":
        if not self.meter_ids and self.transformer_id is None:
            raise ValueError("an event must identify at least one meter or transformer")
        return self


class HypothesisState(ContractModel):
    label: str
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[EvidenceReference] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceReference] = Field(default_factory=list)


class AgentState(ContractModel):
    run_id: UUID
    case_id: UUID | None = None
    trigger_event: EventPayload
    affected_meter_ids: list[str] = Field(default_factory=list)
    goal: str
    status: RunStatus = RunStatus.PENDING
    facts: list[EvidenceReference] = Field(default_factory=list)
    hypotheses: list[HypothesisState] = Field(default_factory=list)
    completed_tool_calls: list[str] = Field(default_factory=list)
    failed_tool_calls: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    proposed_next_step: str | None = None
    approval_status: str | None = None
    verification_result: dict[str, Any] | None = None
    termination_reason: str | None = None
    report_id: UUID | None = None
    revenue_at_risk: dict[str, Any] | None = None
    precedent_case_ids: list[UUID] = Field(default_factory=list)
    triage: dict[str, Any] | None = None
    updated_at: datetime = Field(default_factory=utc_now)
