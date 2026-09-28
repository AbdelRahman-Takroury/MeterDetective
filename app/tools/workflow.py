"""Typed approval-proposal and post-action verification tools."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.common import EvidenceReference
from app.contracts.report import AnswerStatus, InvestigationAnswer
from app.contracts.tool import ToolInput, ToolOutput
from app.db import models
from app.db.repositories import (
    FinanceRepository,
    RankingRepository,
    ReportRepository,
    RepositoryNotFound,
    WorkflowConflict,
    WorkflowRepository,
)
from app.tools.advanced import RevenueRiskOutput


class ProposeActionInput(ToolInput):
    target_case_id: UUID
    source_report_id: UUID
    action_type: str = Field(min_length=1, max_length=80)
    rationale: str = Field(min_length=1, max_length=10_000)
    risk: str = Field(min_length=1, max_length=40)
    requires_approval: bool = True


class ProposeActionOutput(ToolOutput):
    recommendation_id: UUID
    action_id: UUID
    report_id: UUID
    report_version: int
    action_type: str
    risk: str
    requires_approval: bool
    recommendation_status: str
    action_status: str


class VerificationMetrics(ToolOutput):
    status: Literal["recovered", "persistent", "insufficient_observations", "ambiguous"]
    window_start: datetime
    window_end: datetime
    observation_count: int = Field(ge=0)
    valid_observation_count: int = Field(ge=0)
    expected_kwh: float | None = Field(default=None, ge=0)
    observed_kwh: float | None = Field(default=None, ge=0)
    within_baseline_fraction: float | None = Field(default=None, ge=0, le=1)
    peer_agreement_fraction: float | None = Field(default=None, ge=0, le=1)
    median_deviation_fraction: float | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)
    policy_version: str


class VerifyOutcomeInput(ToolInput):
    target_case_id: UUID
    action_id: UUID
    source_report_id: UUID
    metrics: VerificationMetrics
    remaining_revenue_risk: RevenueRiskOutput | None = None


class VerifyOutcomeOutput(ToolOutput):
    case_id: UUID
    action_id: UUID
    outcome: str
    case_status: str
    report_id: UUID
    report_version: int
    evidence_id: UUID
    replan_required: bool


def _latest_answers(
    session: Session, *, case_id: UUID, source_report_id: UUID
) -> tuple[models.InvestigationReport, dict[int, InvestigationAnswer]]:
    report = ReportRepository(session).latest(case_id)
    if report is None or report.id != source_report_id:
        raise WorkflowConflict("Source report is not the latest case report")
    return report, {
        answer.question_id: answer
        for answer in (InvestigationAnswer.model_validate(item) for item in report.answers_json)
    }


def _reference(evidence: models.Evidence, *, observed_at: datetime) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=str(evidence.id),
        source=evidence.source,
        kind=evidence.kind,
        observed_at=observed_at,
        reliability=evidence.reliability,
        metadata=evidence.value_json,
    )


def propose_action_for_approval(
    data: ProposeActionInput, session: Session
) -> ProposeActionOutput:
    _, answers = _latest_answers(
        session, case_id=data.target_case_id, source_report_id=data.source_report_id
    )
    recommendation, action = WorkflowRepository(session).create_recommendation(
        case_id=data.target_case_id,
        action_type=data.action_type,
        rationale=data.rationale,
        risk=data.risk,
        requires_approval=data.requires_approval,
    )
    now = datetime.now(UTC)
    evidence = models.Evidence(
        case_id=data.target_case_id,
        kind="action_recommendation",
        source="propose_action_for_approval",
        value_json={
            "recommendation_id": str(recommendation.id),
            "action_id": str(action.id),
            "action_type": recommendation.action_type,
            "rationale": recommendation.rationale,
            "risk": recommendation.risk,
            "requires_approval": recommendation.requires_approval,
        },
        reliability=1,
        created_at=now,
    )
    session.add(evidence)
    session.flush()
    reference = _reference(evidence, observed_at=now)
    answers[13] = InvestigationAnswer(
        question_id=13,
        status=AnswerStatus.ANSWERED,
        answer=f"The recommended next step is {recommendation.action_type}.",
        structured_values={
            "recommendation_id": str(recommendation.id),
            "action_id": str(action.id),
            "action_type": recommendation.action_type,
            "risk": recommendation.risk,
        },
        confidence=1,
        supporting_evidence=[reference],
        tools_used=["propose_action_for_approval"],
        data_sources=[reference.source],
        limitations=[],
        fresh_as_of=now,
    )
    answers[14] = InvestigationAnswer(
        question_id=14,
        status=AnswerStatus.ANSWERED,
        answer=(
            "Human approval is required before this simulated action."
            if recommendation.requires_approval
            else "This low-impact simulated action does not require human approval."
        ),
        structured_values={
            "requires_approval": recommendation.requires_approval,
            "approval_status": (
                "pending" if recommendation.requires_approval else "not_required"
            ),
        },
        confidence=1,
        supporting_evidence=[reference],
        tools_used=["propose_action_for_approval"],
        data_sources=[reference.source],
        limitations=[],
        fresh_as_of=now,
    )
    report = ReportRepository(session).add_version(
        case_id=data.target_case_id,
        status=(
            "awaiting_approval"
            if recommendation.requires_approval
            else "pending_verification"
        ),
        answers=[answers[index].model_dump(mode="json") for index in range(1, 19)],
        completeness=1,
    )
    session.flush()
    return ProposeActionOutput(
        recommendation_id=recommendation.id,
        action_id=action.id,
        report_id=report.id,
        report_version=report.version,
        action_type=recommendation.action_type,
        risk=recommendation.risk,
        requires_approval=recommendation.requires_approval,
        recommendation_status=recommendation.status,
        action_status=action.status,
        evidence=[reference],
    )


def verify_case_outcome(data: VerifyOutcomeInput, session: Session) -> VerifyOutcomeOutput:
    _, answers = _latest_answers(
        session, case_id=data.target_case_id, source_report_id=data.source_report_id
    )
    action = session.get(models.Action, data.action_id)
    if action is None or action.case_id != data.target_case_id:
        raise RepositoryNotFound("Action not found for case")
    if action.status != "completed":
        raise WorkflowConflict("Only a completed simulated action can be verified")
    approval = (
        session.scalar(
            select(models.Approval).where(
                models.Approval.recommendation_id == action.recommendation_id
            )
        )
        if action.recommendation_id is not None
        else None
    )
    if approval is not None:
        answers[14] = answers[14].model_copy(
            update={
                "structured_values": {
                    **answers[14].structured_values,
                    "approval_status": approval.decision,
                    "decided_by": approval.decided_by,
                    "decided_at": (
                        approval.decided_at.isoformat() if approval.decided_at else None
                    ),
                }
            }
        )

    now = datetime.now(UTC)
    metrics = data.metrics
    evidence = models.Evidence(
        case_id=data.target_case_id,
        kind="post_action_verification",
        source="verify_case_outcome",
        value_json=metrics.model_dump(mode="json", exclude={"evidence", "warnings"}),
        reliability=metrics.confidence,
        created_at=now,
    )
    session.add(evidence)
    session.flush()
    reference = _reference(evidence, observed_at=metrics.window_end)
    definitive = metrics.status in {"recovered", "persistent"}
    if definitive:
        improved = metrics.status == "recovered"
        answers[15] = InvestigationAnswer(
            question_id=15,
            status=AnswerStatus.ANSWERED,
            answer=(
                "The condition improved after the simulated action."
                if improved
                else "The condition remains abnormal after the simulated action."
            ),
            structured_values={
                "outcome": metrics.status,
                **metrics.model_dump(mode="json", exclude={"evidence", "warnings", "status"}),
            },
            confidence=metrics.confidence,
            supporting_evidence=[reference],
            tools_used=["verify_case_outcome"],
            data_sources=[reference.source],
            limitations=metrics.warnings,
            fresh_as_of=metrics.window_end,
        )
    elif metrics.status == "insufficient_observations":
        answers[15] = InvestigationAnswer(
            question_id=15,
            status=AnswerStatus.PENDING_VERIFICATION,
            answer="More post-action readings are required before deciding the outcome.",
            structured_values=metrics.model_dump(
                mode="json", exclude={"evidence", "warnings"}
            ),
            confidence=metrics.confidence,
            supporting_evidence=[reference],
            tools_used=["verify_case_outcome"],
            data_sources=[reference.source],
            limitations=metrics.warnings or ["Minimum observation window has not been met."],
            fresh_as_of=metrics.window_end,
        )
    else:
        answers[15] = InvestigationAnswer(
            question_id=15,
            status=AnswerStatus.UNKNOWN,
            answer="Post-action evidence is conflicting, so improvement is unknown.",
            structured_values=metrics.model_dump(
                mode="json", exclude={"evidence", "warnings"}
            ),
            confidence=metrics.confidence,
            supporting_evidence=[reference],
            tools_used=["verify_case_outcome"],
            data_sources=[reference.source],
            limitations=metrics.warnings or ["Baseline and peer signals do not agree."],
            fresh_as_of=metrics.window_end,
        )

    revenue = data.remaining_revenue_risk
    if revenue is not None and revenue.status == "answered":
        answers[16] = InvestigationAnswer(
            question_id=16,
            status=AnswerStatus.ANSWERED,
            answer="Remaining Revenue at Risk was refreshed after the simulated action.",
            structured_values=revenue.values,
            confidence=revenue.confidence,
            supporting_evidence=[reference],
            tools_used=["estimate_revenue_at_risk", "verify_case_outcome"],
            data_sources=[reference.source],
            limitations=revenue.values.get("assumptions", []),
            fresh_as_of=metrics.window_end,
        )

    case = session.get(models.Case, data.target_case_id)
    if case is None:
        raise RepositoryNotFound("Case not found")
    if metrics.status == "recovered":
        case.status = "resolved"
        case.active_rank = None
        answers[18] = InvestigationAnswer(
            question_id=18,
            status=AnswerStatus.NOT_APPLICABLE,
            answer="The resolved case is no longer in the active triage queue.",
            structured_values={"active": False, "previous_band": case.priority_band},
            confidence=1,
            supporting_evidence=[reference],
            tools_used=["verify_case_outcome"],
            data_sources=[reference.source],
            limitations=["Resolved cases are excluded from active queue ranking."],
            fresh_as_of=metrics.window_end,
        )
    elif metrics.status == "persistent":
        case.status = "reopened"
    else:
        case.status = "monitoring"

    refreshed_triage: dict[str, object] | None = None
    if metrics.status != "recovered":
        previous_triage = session.scalar(
            select(models.TriageAssessment)
            .join(
                models.InvestigationReport,
                models.InvestigationReport.id == models.TriageAssessment.report_id,
            )
            .where(models.TriageAssessment.case_id == case.id)
            .order_by(models.InvestigationReport.version.desc())
            .limit(1)
        )
        if previous_triage is not None:
            active = [
                item
                for item in RankingRepository(session).active_cases(limit=500)
                if item.triage_score is not None
            ]
            scores = [float(item.triage_score) for item in active]
            score = float(previous_triage.score)
            active_count = max(1, len(active))
            active_rank = 1 + sum(item > score for item in scores)
            percentile = round(
                100.0
                if active_count == 1
                else 100 * sum(item < score for item in scores) / (active_count - 1),
                2,
            )
            refreshed_triage = {
                "score": score,
                "band": previous_triage.band,
                "active_rank": active_rank,
                "active_count": active_count,
                "percentile": percentile,
                "factors": {
                    **previous_triage.factors_json,
                    "verification_outcome": metrics.status,
                },
                "policy_version": previous_triage.policy_version,
            }
            answers[18] = InvestigationAnswer(
                question_id=18,
                status=AnswerStatus.ANSWERED,
                answer=(
                    f"Triage remains {previous_triage.band}, rank {active_rank} "
                    f"of {active_count} after verification."
                ),
                structured_values=refreshed_triage,
                confidence=metrics.confidence,
                supporting_evidence=[reference],
                tools_used=["calculate_triage_priority", "verify_case_outcome"],
                data_sources=[reference.source],
                limitations=metrics.warnings,
                fresh_as_of=metrics.window_end,
            )

    report_status = "complete" if definitive else "pending_verification"
    report = ReportRepository(session).add_version(
        case_id=case.id,
        status=report_status,
        answers=[answers[index].model_dump(mode="json") for index in range(1, 19)],
        completeness=1,
    )
    if refreshed_triage is not None:
        RankingRepository(session).add_assessment(
            case_id=case.id,
            report_id=report.id,
            score=float(refreshed_triage["score"]),
            band=str(refreshed_triage["band"]),
            active_rank=int(refreshed_triage["active_rank"]),
            active_count=int(refreshed_triage["active_count"]),
            percentile=float(refreshed_triage["percentile"]),
            factors=dict(refreshed_triage["factors"]),
            policy_version=str(refreshed_triage["policy_version"]),
        )
    if (
        revenue is not None
        and revenue.status == "answered"
        and revenue.tariff_id is not None
    ):
        values = revenue.values
        missing = float(values["expected_missing_kwh"])
        risk = values["revenue_at_risk_jod"]
        FinanceRepository(session).add(
            case_id=case.id,
            report_id=report.id,
            tariff_id=revenue.tariff_id,
            missing_kwh=(missing, missing, missing),
            risk_jod=(
                Decimal(str(risk["low"])),
                Decimal(str(risk["base"])),
                Decimal(str(risk["high"])),
            ),
            assumptions=[
                *values.get("assumptions", []),
                {"method_version": revenue.method_version},
            ],
            confidence=revenue.confidence,
        )
    event_type = "verification_completed" if definitive else "verification_deferred"
    session.add(
        models.CaseEvent(
            case_id=case.id,
            event_type=event_type,
            details_json={
                "action_id": str(action.id),
                "outcome": metrics.status,
                "report_id": str(report.id),
                "report_version": report.version,
                "evidence_id": str(evidence.id),
                "window_start": metrics.window_start.isoformat(),
                "window_end": metrics.window_end.isoformat(),
                "replan_required": metrics.status == "persistent",
            },
            created_at=now,
        )
    )
    session.flush()
    return VerifyOutcomeOutput(
        case_id=case.id,
        action_id=action.id,
        outcome=metrics.status,
        case_status=case.status,
        report_id=report.id,
        report_version=report.version,
        evidence_id=evidence.id,
        replan_required=metrics.status == "persistent",
        evidence=[reference],
        warnings=metrics.warnings,
    )
