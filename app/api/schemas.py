"""Public response shapes for the initial read APIs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MeterSummary(OrmResponse):
    id: str
    transformer_id: UUID | None
    location: str | None
    type: str
    status: str
    customer_segment: str | None
    has_solar: bool | None
    has_ev: bool | None


class MeterDetail(MeterSummary):
    metadata_source: str | None
    metadata_json: dict[str, Any]


class MeterListResponse(BaseModel):
    items: list[MeterSummary]
    offset: int
    limit: int


class ReadingResponse(OrmResponse):
    id: UUID
    meter_id: str
    timestamp: datetime
    kwh: float
    quality_flag: str
    source: str


class ReadingWindowResponse(BaseModel):
    meter_id: str
    start: datetime
    end: datetime
    items: list[ReadingResponse]
    limit: int


class CaseSummary(OrmResponse):
    id: UUID
    title: str
    status: str
    triage_score: float | None
    priority_band: str | None
    active_rank: int | None
    confidence: float | None
    assigned_to: str | None
    opened_at: datetime
    updated_at: datetime


class CaseListResponse(BaseModel):
    items: list[CaseSummary]
    offset: int
    limit: int


class QueueItem(CaseSummary):
    meter_ids: list[str]
    report_version: int | None
    answered_count: int
    present_count: int
    anomaly_type: str | None
    revenue_jod: dict[str, Any] | None
    precedent_count: int | None
    recommendation_status: str | None


class QueueResponse(BaseModel):
    items: list[QueueItem]
    total: int
    offset: int
    limit: int


class ReplayStepRequest(BaseModel):
    meter_id: str
    event_time: datetime
    lookback_days: int = 35
    idempotency_key: str | None = None


class ReplayStepResponse(BaseModel):
    event_id: UUID
    run_id: UUID
    case_id: UUID | None
    report_id: UUID | None
    status: str
    duplicate: bool
    anomaly_count: int
    tool_calls: int


class ScenarioResetResponse(BaseModel):
    scenario_id: str
    meter_id: str
    event_time: datetime
    case_id: UUID
    report_id: UUID
    run_id: UUID
    repair_window_start: datetime
    repair_window_end: datetime
    reset_records: dict[str, int]


class CaseDetailResponse(BaseModel):
    case: CaseSummary
    meter_ids: list[str]
    evidence: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    latest_report: dict[str, Any] | None
    case_events: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    report_coverage: dict[str, Any]
    financial_impact: dict[str, Any] | None
    triage_assessment: dict[str, Any] | None
    precedents: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    scenario: dict[str, Any] | None


class CaseTraceResponse(BaseModel):
    case_id: UUID
    runs: list[dict[str, Any]]


class RecommendationCreateRequest(BaseModel):
    action_type: str = Field(min_length=1, max_length=80)
    rationale: str = Field(min_length=1, max_length=10_000)
    risk: str = Field(min_length=1, max_length=40)
    requires_approval: bool = True


class RecommendationResponse(OrmResponse):
    id: UUID
    case_id: UUID
    action_type: str
    rationale: str
    risk: str
    requires_approval: bool
    status: str
    created_at: datetime
    updated_at: datetime


class ApprovalDecisionRequest(BaseModel):
    decided_by: str = Field(min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=10_000)


class ApprovalResponse(OrmResponse):
    id: UUID
    recommendation_id: UUID
    decision: str
    decided_by: str | None
    decided_at: datetime | None
    comment: str | None


class ActionResponse(OrmResponse):
    id: UUID
    case_id: UUID
    recommendation_id: UUID | None
    action_type: str
    status: str
    result_json: dict[str, Any]
    created_at: datetime
    executed_at: datetime | None


class RecommendationWorkflowResponse(BaseModel):
    recommendation: RecommendationResponse
    approval: ApprovalResponse | None
    action: ActionResponse


class SimulationExecuteRequest(BaseModel):
    result: dict[str, Any] = Field(default_factory=dict)


class VerifyCaseRequest(BaseModel):
    action_id: UUID
    window_start: datetime
    window_end: datetime


class VerifyCaseResponse(BaseModel):
    case_id: UUID
    action_id: UUID
    outcome: str
    case_status: str
    report_id: UUID
    report_version: int
    evidence_id: UUID
    replan_required: bool
    warnings: list[str]
