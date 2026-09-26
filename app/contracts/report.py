from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.contracts.common import ContractModel, EvidenceReference, utc_now


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    PENDING_VERIFICATION = "pending_verification"


class ReportStatus(StrEnum):
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    PENDING_VERIFICATION = "pending_verification"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class InvestigationAnswer(ContractModel):
    question_id: int = Field(ge=1, le=18)
    status: AnswerStatus
    answer: str
    structured_values: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[EvidenceReference] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceReference] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    fresh_as_of: datetime | None = None

    @model_validator(mode="after")
    def substantiate_answer(self) -> Self:
        if self.status is AnswerStatus.ANSWERED and not self.supporting_evidence:
            raise ValueError("answered questions require at least one evidence reference")
        if self.status is not AnswerStatus.ANSWERED and not self.limitations:
            raise ValueError("unknown or not-applicable answers require a limitation")
        return self


class RevenueAtRisk(ContractModel):
    currency: str = Field(pattern="^JOD$")
    low: float = Field(ge=0)
    base: float = Field(ge=0)
    high: float = Field(ge=0)
    tariff_version: str
    assumptions: list[str]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered_range(self) -> Self:
        if not self.low <= self.base <= self.high:
            raise ValueError("revenue range must satisfy low <= base <= high")
        return self


class TriageAssessment(ContractModel):
    score: float = Field(ge=0, le=100)
    band: str = Field(pattern="^P[1-4]$")
    active_rank: int = Field(ge=1)
    active_count: int = Field(ge=1)
    factors: dict[str, float]
    policy_version: str


class InvestigationReport(ContractModel):
    report_id: UUID
    case_id: UUID
    version: int = Field(ge=1)
    status: ReportStatus
    answers: list[InvestigationAnswer]
    revenue_at_risk: RevenueAtRisk | None = None
    precedent_case_ids: list[UUID] = Field(default_factory=list)
    meter_pattern_summary: str = "No precedent analysis has been completed."
    triage: TriageAssessment | None = None
    generated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_all_questions_once(self) -> Self:
        ids = [answer.question_id for answer in self.answers]
        expected = list(range(1, 19))
        if sorted(ids) != expected:
            raise ValueError("answers must contain each question ID from 1 through 18 exactly once")
        verification = next(answer for answer in self.answers if answer.question_id == 15)
        if (
            self.status is ReportStatus.COMPLETE
            and verification.status is not AnswerStatus.ANSWERED
        ):
            raise ValueError("a complete report requires an answered verification question")
        return self

    @property
    def completeness(self) -> float:
        return len({answer.question_id for answer in self.answers}) / 18

    @property
    def answered_fraction(self) -> float:
        answered = sum(answer.status is AnswerStatus.ANSWERED for answer in self.answers)
        return answered / 18
