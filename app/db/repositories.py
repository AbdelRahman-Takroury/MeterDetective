"""Transaction-neutral persistence operations for the Day 2-B read/write paths.

Callers own commit/rollback. Methods flush new rows so their generated IDs are usable
within the same transaction; they never commit independently.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import models


class RepositoryConflict(ValueError):
    """A natural key exists with different values."""


class RepositoryNotFound(LookupError):
    """A requested parent record does not exist."""


class WorkflowConflict(ValueError):
    """A workflow transition is incompatible with the persisted state."""


class ApprovalRequired(PermissionError):
    """An action cannot execute because its recommendation is not approved."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return value.astimezone(UTC)


def _window(start: datetime, end: datetime, limit: int) -> tuple[datetime, datetime]:
    start, end = _utc(start), _utc(end)
    if end <= start:
        raise ValueError("Window end must be after start")
    if not 1 <= limit <= 10_000:
        raise ValueError("Limit must be between 1 and 10000")
    return start, end


class MeterRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, meter_id: str) -> models.Meter | None:
        return self.session.get(models.Meter, meter_id)

    def list(self, *, offset: int = 0, limit: int = 100) -> list[models.Meter]:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("Invalid meter page")
        return list(
            self.session.scalars(
                select(models.Meter).order_by(models.Meter.id).offset(offset).limit(limit)
            )
        )


class ReadingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def window(
        self, meter_id: str, start: datetime, end: datetime, *, limit: int = 1000
    ) -> list[models.Reading]:
        start, end = _window(start, end, limit)
        return list(
            self.session.scalars(
                select(models.Reading)
                .where(
                    models.Reading.meter_id == meter_id,
                    models.Reading.timestamp >= start,
                    models.Reading.timestamp < end,
                )
                .order_by(models.Reading.timestamp, models.Reading.id)
                .limit(limit)
            )
        )

    def transformer_window(
        self, transformer_id: UUID, start: datetime, end: datetime, *, limit: int = 1000
    ) -> list[models.TransformerReading]:
        start, end = _window(start, end, limit)
        return list(
            self.session.scalars(
                select(models.TransformerReading)
                .where(
                    models.TransformerReading.transformer_id == transformer_id,
                    models.TransformerReading.timestamp >= start,
                    models.TransformerReading.timestamp < end,
                )
                .order_by(models.TransformerReading.timestamp)
                .limit(limit)
            )
        )

    def add(
        self,
        *,
        meter_id: str,
        timestamp: datetime,
        kwh: float,
        source: str,
        quality_flag: str = "valid",
    ) -> models.Reading:
        timestamp = _utc(timestamp)
        existing = self.session.scalar(
            select(models.Reading).where(
                models.Reading.meter_id == meter_id,
                models.Reading.timestamp == timestamp,
            )
        )
        if existing is not None:
            if (
                existing.kwh != kwh
                or existing.source != source
                or existing.quality_flag != quality_flag
            ):
                raise RepositoryConflict("Reading already exists with different values")
            return existing
        reading = models.Reading(
            meter_id=meter_id,
            timestamp=timestamp,
            kwh=kwh,
            source=source,
            quality_flag=quality_flag,
        )
        self.session.add(reading)
        self.session.flush()
        return reading


class EventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, idempotency_key: str) -> models.Event | None:
        return self.session.scalar(
            select(models.Event).where(models.Event.idempotency_key == idempotency_key)
        )

    def add(
        self, *, idempotency_key: str, event_type: str, payload: dict[str, Any], status: str
    ) -> models.Event:
        existing = self.get(idempotency_key)
        if existing is not None:
            if existing.event_type != event_type or existing.payload_json != payload:
                raise RepositoryConflict("Event key already exists with different values")
            return existing
        event = models.Event(
            idempotency_key=idempotency_key,
            event_type=event_type,
            payload_json=payload,
            status=status,
        )
        self.session.add(event)
        self.session.flush()
        return event


class CaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, case_id: UUID) -> models.Case | None:
        return self.session.get(models.Case, case_id)

    def list(
        self, *, status: str | None = None, offset: int = 0, limit: int = 100
    ) -> list[models.Case]:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("Invalid case page")
        statement = select(models.Case)
        if status is not None:
            statement = statement.where(models.Case.status == status)
        return list(
            self.session.scalars(
                statement.order_by(models.Case.opened_at.desc(), models.Case.id)
                .offset(offset)
                .limit(limit)
            )
        )

    def add(self, *, title: str, status: str, meter_ids: list[str] | None = None) -> models.Case:
        case = models.Case(title=title, status=status)
        self.session.add(case)
        self.session.flush()
        for meter_id in dict.fromkeys(meter_ids or []):
            self.session.add(
                models.CaseMeter(case_id=case.id, meter_id=meter_id, relationship="affected")
            )
        self.session.flush()
        return case


class ReportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest(self, case_id: UUID) -> models.InvestigationReport | None:
        return self.session.scalar(
            select(models.InvestigationReport)
            .where(models.InvestigationReport.case_id == case_id)
            .order_by(models.InvestigationReport.version.desc())
            .limit(1)
        )

    def versions(self, case_id: UUID) -> list[models.InvestigationReport]:
        return list(
            self.session.scalars(
                select(models.InvestigationReport)
                .where(models.InvestigationReport.case_id == case_id)
                .order_by(models.InvestigationReport.version)
            )
        )

    def add_version(
        self, *, case_id: UUID, status: str, answers: list[Any], completeness: float
    ) -> models.InvestigationReport:
        from app.services.report_validation import (
            is_contract_report_status,
            validate_report_for_persistence,
        )

        if is_contract_report_status(status):
            validated = validate_report_for_persistence(
                case_id=case_id, status=status, answers=answers
            )
            completeness = validated.completeness
        parent = self.session.scalar(
            select(models.Case).where(models.Case.id == case_id).with_for_update()
        )
        if parent is None:
            raise RepositoryNotFound("Case not found")
        previous = self.latest(case_id)
        now = datetime.now(UTC)
        if previous is not None:
            previous.superseded_at = now
        report = models.InvestigationReport(
            case_id=case_id,
            version=1 if previous is None else previous.version + 1,
            status=status,
            answers_json=answers,
            completeness=completeness,
            generated_at=now,
        )
        self.session.add(report)
        self.session.flush()
        return report


class HistoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def case_events(self, case_id: UUID, *, limit: int = 500) -> list[models.CaseEvent]:
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid history limit")
        return list(
            self.session.scalars(
                select(models.CaseEvent)
                .where(models.CaseEvent.case_id == case_id)
                .order_by(models.CaseEvent.created_at, models.CaseEvent.id)
                .limit(limit)
            )
        )

    def meter_cases(
        self, meter_id: str, *, closed_only: bool = False, limit: int = 100,
        exclude_case_id: UUID | None = None,
    ) -> list[models.Case]:
        if not 1 <= limit <= 500:
            raise ValueError("Invalid history limit")
        statement = (
            select(models.Case).join(models.CaseMeter).where(models.CaseMeter.meter_id == meter_id)
        )
        if closed_only:
            statement = statement.where(models.Case.status == "closed")
        if exclude_case_id is not None:
            statement = statement.where(models.Case.id != exclude_case_id)
        return list(
            self.session.scalars(
                statement.order_by(models.Case.opened_at.desc(), models.Case.id).limit(limit)
            )
        )


class HypothesisRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, case_id: UUID) -> list[models.Hypothesis]:
        return list(
            self.session.scalars(
                select(models.Hypothesis)
                .where(models.Hypothesis.case_id == case_id)
                .order_by(models.Hypothesis.confidence.desc(), models.Hypothesis.label)
            )
        )

    def update_confidence(
        self,
        hypothesis_id: UUID,
        *,
        confidence: float,
        reason: str,
        supporting_evidence_ids: list[UUID] | None = None,
        contradicting_evidence_ids: list[UUID] | None = None,
    ) -> models.Hypothesis:
        if not 0 <= confidence <= 1:
            raise ValueError("Hypothesis confidence must be between 0 and 1")
        if not reason.strip():
            raise ValueError("Hypothesis updates require a reason")
        hypothesis = self.session.get(models.Hypothesis, hypothesis_id)
        if hypothesis is None:
            raise RepositoryNotFound("Hypothesis not found")
        support = supporting_evidence_ids or []
        contradiction = contradicting_evidence_ids or []
        for evidence_id in [*support, *contradiction]:
            evidence = self.session.get(models.Evidence, evidence_id)
            if evidence is None or evidence.case_id != hypothesis.case_id:
                raise RepositoryNotFound("Evidence does not belong to the hypothesis case")
        previous = hypothesis.confidence
        hypothesis.confidence = confidence
        hypothesis.support_json = list(
            dict.fromkeys([*hypothesis.support_json, *(str(item) for item in support)])
        )
        hypothesis.contradiction_json = list(
            dict.fromkeys([*hypothesis.contradiction_json, *(str(item) for item in contradiction)])
        )
        history = list(hypothesis.update_history_json)
        now = datetime.now(UTC)
        history.append(
            {
                "at": now.isoformat(),
                "reason": reason,
                "previous_confidence": previous,
                "confidence": confidence,
                "supporting_evidence": [str(item) for item in support],
                "contradicting_evidence": [str(item) for item in contradiction],
            }
        )
        hypothesis.update_history_json = history
        hypothesis.updated_at = now
        self.session.add(
            models.CaseEvent(
                case_id=hypothesis.case_id,
                event_type="hypothesis_updated",
                details_json={
                    "hypothesis_id": str(hypothesis.id),
                    "label": hypothesis.label,
                    "previous_confidence": previous,
                    "confidence": confidence,
                    "reason": reason,
                },
            )
        )
        self.session.flush()
        return hypothesis


class WorkflowRepository:
    """Persistence boundary for recommendation, approval, and simulated-action state."""

    APPROVAL_RISKS = frozenset({"medium", "high", "critical"})
    HIGH_IMPACT_ACTIONS = frozenset(
        {
            "technician_inspection",
            "transformer_inspection",
            "work_order",
            "meter_repair",
            "meter_replacement",
        }
    )
    LOW_IMPACT_ACTIONS = frozenset({"continue_monitoring"})

    def __init__(self, session: Session) -> None:
        self.session = session

    @classmethod
    def approval_is_required(
        cls, *, action_type: str, risk: str, requested: bool
    ) -> bool:
        normalized_action = action_type.strip().lower()
        normalized_risk = risk.strip().lower()
        return (
            requested
            or normalized_risk in cls.APPROVAL_RISKS
            or normalized_action in cls.HIGH_IMPACT_ACTIONS
            # New or unclassified actions default to the safe, approval-gated path.
            or normalized_action not in cls.LOW_IMPACT_ACTIONS
        )

    def create_recommendation(
        self,
        *,
        case_id: UUID,
        plan_id: UUID | None = None,
        source_report_id: UUID | None = None,
        action_type: str,
        rationale: str,
        risk: str,
        requires_approval: bool,
    ) -> tuple[models.Recommendation, models.Action]:
        case = self.session.scalar(
            select(models.Case).where(models.Case.id == case_id).with_for_update()
        )
        if case is None:
            raise RepositoryNotFound("Case not found")
        if plan_id is not None:
            plan = self.session.get(models.InvestigationPlan, plan_id)
            if plan is None or plan.case_id != case_id or plan.status != "active":
                raise WorkflowConflict("Recommendation plan is not active for this case")
        if source_report_id is not None:
            report = self.session.get(models.InvestigationReport, source_report_id)
            if report is None or report.case_id != case_id:
                raise WorkflowConflict("Recommendation report does not belong to this case")
        action_type = action_type.strip()
        rationale = rationale.strip()
        risk = risk.strip().lower()
        if not action_type or not rationale or not risk:
            raise ValueError("Recommendation fields cannot be blank")
        gated = self.approval_is_required(
            action_type=action_type, risk=risk, requested=requires_approval
        )
        now = datetime.now(UTC)
        recommendation = models.Recommendation(
            case_id=case_id,
            plan_id=plan_id,
            source_report_id=source_report_id,
            action_type=action_type,
            rationale=rationale,
            risk=risk,
            requires_approval=gated,
            status="pending_approval" if gated else "approved",
            created_at=now,
        )
        self.session.add(recommendation)
        self.session.flush()
        action = models.Action(
            case_id=case_id,
            recommendation_id=recommendation.id,
            action_type=action_type,
            status="awaiting_approval" if gated else "ready",
            result_json={},
            created_at=now,
        )
        self.session.add(action)
        self.session.flush()
        case.status = "awaiting_approval" if gated else "action_ready"
        now = datetime.now(UTC)
        self.session.add(
            models.CaseEvent(
                case_id=case_id,
                event_type="recommendation_created",
                details_json={
                    "recommendation_id": str(recommendation.id),
                    "action_id": str(action.id),
                    "action_type": action_type,
                    "risk": risk,
                    "requires_approval": gated,
                },
                created_at=now,
            )
        )
        self.session.flush()
        return recommendation, action

    def get_recommendation(self, recommendation_id: UUID) -> models.Recommendation | None:
        return self.session.get(models.Recommendation, recommendation_id)

    def action_for_recommendation(self, recommendation_id: UUID) -> models.Action | None:
        return self.session.scalar(
            select(models.Action).where(models.Action.recommendation_id == recommendation_id)
        )

    def approval_for_recommendation(self, recommendation_id: UUID) -> models.Approval | None:
        return self.session.scalar(
            select(models.Approval).where(models.Approval.recommendation_id == recommendation_id)
        )

    def decide(
        self,
        recommendation_id: UUID,
        *,
        decision: str,
        decided_by: str,
        comment: str | None,
    ) -> tuple[models.Recommendation, models.Approval, models.Action]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decision must be approved or rejected")
        recommendation = self.session.scalar(
            select(models.Recommendation)
            .where(models.Recommendation.id == recommendation_id)
            .with_for_update()
        )
        if recommendation is None:
            raise RepositoryNotFound("Recommendation not found")
        action = self.action_for_recommendation(recommendation_id)
        if action is None:
            raise WorkflowConflict("Recommendation has no associated action")
        existing = self.approval_for_recommendation(recommendation_id)
        if existing is not None:
            if existing.decision != decision:
                raise WorkflowConflict(
                    f"Recommendation was already {existing.decision}; decisions are final"
                )
            return recommendation, existing, action
        if not recommendation.requires_approval:
            raise WorkflowConflict("Recommendation does not require human approval")
        if recommendation.status != "pending_approval" or action.status != "awaiting_approval":
            raise WorkflowConflict("Recommendation is no longer awaiting approval")

        now = datetime.now(UTC)
        approval = models.Approval(
            recommendation_id=recommendation.id,
            decision=decision,
            decided_by=decided_by.strip(),
            decided_at=now,
            comment=comment.strip() if comment else None,
        )
        self.session.add(approval)
        recommendation.status = decision
        action.status = "ready" if decision == "approved" else "rejected"
        case = self.session.get(models.Case, recommendation.case_id)
        if case is None:
            raise RepositoryNotFound("Case not found")
        case.status = "action_ready" if decision == "approved" else "monitoring"
        self.session.add(
            models.CaseEvent(
                case_id=recommendation.case_id,
                event_type="approval_decided",
                details_json={
                    "recommendation_id": str(recommendation.id),
                    "action_id": str(action.id),
                    "decision": decision,
                    "decided_by": approval.decided_by,
                    "comment": approval.comment,
                },
                created_at=now,
            )
        )
        self.session.flush()
        return recommendation, approval, action

    def execute_simulation(
        self, action_id: UUID, *, result: dict[str, Any]
    ) -> tuple[models.Recommendation, models.Approval | None, models.Action]:
        action = self.session.scalar(
            select(models.Action).where(models.Action.id == action_id).with_for_update()
        )
        if action is None:
            raise RepositoryNotFound("Action not found")
        if action.status == "completed":
            raise WorkflowConflict("Action simulation has already completed")
        if action.status == "rejected":
            raise ApprovalRequired("Rejected actions cannot be executed")
        if action.recommendation_id is None:
            raise ApprovalRequired("Action is not linked to a recommendation")
        recommendation = self.get_recommendation(action.recommendation_id)
        if recommendation is None or recommendation.case_id != action.case_id:
            raise WorkflowConflict("Action recommendation is invalid")
        if action.action_type != recommendation.action_type:
            raise WorkflowConflict("Action type does not match its recommendation")
        approval = self.approval_for_recommendation(recommendation.id)
        action_requires_approval = self.approval_is_required(
            action_type=action.action_type,
            risk=recommendation.risk,
            requested=recommendation.requires_approval,
        )
        if action_requires_approval and (
            approval is None or approval.decision != "approved"
        ):
            raise ApprovalRequired("Action requires an approved recommendation")
        if action.status != "ready":
            raise WorkflowConflict(f"Action in status '{action.status}' cannot execute")

        now = datetime.now(UTC)
        action.status = "completed"
        action.result_json = result
        action.executed_at = now
        recommendation.status = "executed"
        case = self.session.get(models.Case, action.case_id)
        if case is None:
            raise RepositoryNotFound("Case not found")
        case.status = "pending_verification"
        self.session.add(
            models.CaseEvent(
                case_id=action.case_id,
                event_type="action_completed",
                details_json={
                    "recommendation_id": str(recommendation.id),
                    "action_id": str(action.id),
                    "action_type": action.action_type,
                    "simulation": True,
                    "result": result,
                },
                created_at=now,
            )
        )
        self.session.flush()
        return recommendation, approval, action


class TariffRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def effective(self, segment: str, at: datetime) -> list[models.Tariff]:
        at = _utc(at)
        return list(
            self.session.scalars(
                select(models.Tariff)
                .where(
                    models.Tariff.customer_segment == segment,
                    models.Tariff.effective_from <= at,
                    or_(models.Tariff.effective_to.is_(None), models.Tariff.effective_to > at),
                )
                .order_by(models.Tariff.name)
            )
        )


class FinanceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_report(self, report_id: UUID) -> models.FinancialImpact | None:
        return self.session.scalar(
            select(models.FinancialImpact).where(models.FinancialImpact.report_id == report_id)
        )

    def add(
        self,
        *,
        case_id: UUID,
        report_id: UUID,
        tariff_id: UUID,
        missing_kwh: tuple[float, float, float],
        risk_jod: tuple[Decimal, Decimal, Decimal],
        assumptions: list[Any],
        confidence: float,
    ) -> models.FinancialImpact:
        if self.by_report(report_id) is not None:
            raise RepositoryConflict("Financial impact already exists for report")
        report = self.session.get(models.InvestigationReport, report_id)
        if report is None or report.case_id != case_id:
            raise RepositoryNotFound("Report does not belong to case")
        impact = models.FinancialImpact(
            case_id=case_id,
            report_id=report_id,
            tariff_id=tariff_id,
            missing_kwh_low=missing_kwh[0],
            missing_kwh_base=missing_kwh[1],
            missing_kwh_high=missing_kwh[2],
            risk_jod_low=risk_jod[0],
            risk_jod_base=risk_jod[1],
            risk_jod_high=risk_jod[2],
            assumptions_json=assumptions,
            confidence=confidence,
        )
        self.session.add(impact)
        self.session.flush()
        return impact


class RankingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def active_cases(
        self,
        *,
        statuses: tuple[str, ...] = (
            "open",
            "investigating",
            "triaged",
            "awaiting_approval",
            "action_ready",
            "pending_verification",
            "monitoring",
            "reopened",
        ),
        limit: int = 100,
    ) -> list[models.Case]:
        if not 1 <= limit <= 500:
            raise ValueError("Invalid ranking limit")
        if not statuses:
            return []
        return list(
            self.session.scalars(
                select(models.Case)
                .where(models.Case.status.in_(statuses))
                .order_by(
                    models.Case.priority_band.asc().nulls_last(),
                    models.Case.active_rank.asc().nulls_last(),
                    models.Case.triage_score.desc().nulls_last(),
                    models.Case.opened_at,
                    models.Case.id,
                )
                .limit(limit)
            )
        )

    def by_report(self, report_id: UUID) -> models.TriageAssessment | None:
        return self.session.scalar(
            select(models.TriageAssessment).where(models.TriageAssessment.report_id == report_id)
        )

    def add_assessment(
        self,
        *,
        case_id: UUID,
        report_id: UUID,
        score: float,
        band: str,
        active_rank: int,
        active_count: int,
        percentile: float,
        factors: dict[str, Any],
        policy_version: str,
    ) -> models.TriageAssessment:
        if self.by_report(report_id) is not None:
            raise RepositoryConflict("Triage assessment already exists for report")
        report = self.session.get(models.InvestigationReport, report_id)
        if report is None or report.case_id != case_id:
            raise RepositoryNotFound("Report does not belong to case")
        case = self.session.get(models.Case, case_id)
        if case is None:
            raise RepositoryNotFound("Case not found")
        latest_report_id = self.session.scalar(
            select(models.InvestigationReport.id)
            .where(models.InvestigationReport.case_id == case_id)
            .order_by(models.InvestigationReport.version.desc())
            .limit(1)
        )
        if latest_report_id != report_id:
            raise RepositoryConflict("Cannot rank a superseded report")
        assessment = models.TriageAssessment(
            case_id=case_id,
            report_id=report_id,
            score=score,
            band=band,
            active_rank=active_rank,
            active_count=active_count,
            percentile=percentile,
            factors_json=factors,
            policy_version=policy_version,
        )
        self.session.add(assessment)
        case.triage_score = score
        case.priority_band = band
        case.active_rank = active_rank
        self.session.flush()
        return assessment
