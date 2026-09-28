"""Application orchestration for action proposals and post-action verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.common import RunStatus
from app.core.config import get_settings
from app.db import models
from app.db.repositories import ReadingRepository, ReportRepository, TariffRepository
from app.tools.advanced import RevenueRiskInput, RevenueRiskOutput
from app.tools.analytics import (
    BaselineInput,
    BaselineOutput,
    QualityInput,
    QualityOutput,
    ReadingPoint,
)
from app.tools.registry import ToolRegistry
from app.tools.verification import (
    ObservationWindowResult,
    PeerAgreementResult,
    ReturnToBaselineResult,
    summarize_verification,
)
from app.tools.workflow import (
    ProposeActionInput,
    ProposeActionOutput,
    VerificationMetrics,
    VerifyOutcomeInput,
    VerifyOutcomeOutput,
)


class CaseWorkflowFailure(RuntimeError):
    """A proposal or verification workflow could not complete safely."""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CaseWorkflowService:
    def __init__(self, session: Session, registry: ToolRegistry) -> None:
        self.session = session
        self.registry = registry

    def _execute(self, name: str, tool_input, output_type):  # noqa: ANN001
        execution = self.registry.execute(name, tool_input, self.session)
        if execution.status is not RunStatus.SUCCEEDED or execution.output is None:
            message = execution.error.message if execution.error else f"{name} failed"
            raise CaseWorkflowFailure(message)
        return cast(Any, output_type.model_validate(execution.output.model_dump()))

    def _run(self, *, case_id: UUID, event_type: str, payload: dict[str, Any]) -> models.AgentRun:
        run = models.AgentRun(
            case_id=case_id,
            trigger={"event_type": event_type, **payload},
            status="running",
            state_json={"stage": "started", "completed_tools": []},
        )
        self.session.add(run)
        self.session.flush()
        return run

    def propose(
        self,
        *,
        case_id: UUID,
        action_type: str,
        rationale: str,
        risk: str,
        requires_approval: bool,
    ) -> ProposeActionOutput:
        report = ReportRepository(self.session).latest(case_id)
        if report is None:
            raise CaseWorkflowFailure("Case has no investigation report")
        run = self._run(
            case_id=case_id,
            event_type="action.proposal_requested",
            payload={"case_id": str(case_id), "source_report_id": str(report.id)},
        )
        try:
            output = self._execute(
                "propose_action_for_approval",
                ProposeActionInput(
                    run_id=run.id,
                    case_id=case_id,
                    target_case_id=case_id,
                    source_report_id=report.id,
                    action_type=action_type,
                    rationale=rationale,
                    risk=risk,
                    requires_approval=requires_approval,
                ),
                ProposeActionOutput,
            )
            run.status = "succeeded"
            run.ended_at = datetime.now(UTC)
            run.state_json = {
                "stage": "awaiting_approval" if output.requires_approval else "action_ready",
                "recommendation_id": str(output.recommendation_id),
                "action_id": str(output.action_id),
                "report_id": str(output.report_id),
                "completed_tools": ["propose_action_for_approval"],
                "approval_status": "pending" if output.requires_approval else "not_required",
            }
            self.session.flush()
            return output
        except Exception as exc:
            run.status = "failed"
            run.ended_at = datetime.now(UTC)
            run.state_json = {"stage": "failed", "error": type(exc).__name__}
            self.session.flush()
            raise

    def _existing_verification(
        self,
        *,
        case: models.Case,
        action: models.Action,
        window_start: datetime,
        window_end: datetime,
    ) -> VerifyOutcomeOutput | None:
        events = list(
            self.session.scalars(
                select(models.CaseEvent)
                .where(
                    models.CaseEvent.case_id == case.id,
                    models.CaseEvent.event_type.in_(
                        ("verification_completed", "verification_deferred")
                    ),
                )
                .order_by(models.CaseEvent.created_at.desc(), models.CaseEvent.id.desc())
            )
        )
        for event in events:
            details = event.details_json
            if (
                details.get("action_id") != str(action.id)
                or details.get("window_start") != window_start.isoformat()
                or details.get("window_end") != window_end.isoformat()
            ):
                continue
            report_id = UUID(details["report_id"])
            report = self.session.get(models.InvestigationReport, report_id)
            if report is None:
                continue
            return VerifyOutcomeOutput(
                case_id=case.id,
                action_id=action.id,
                outcome=details["outcome"],
                case_status=case.status,
                report_id=report.id,
                report_version=report.version,
                evidence_id=UUID(details["evidence_id"]),
                replan_required=bool(details["replan_required"]),
            )
        return None

    def verify(
        self,
        *,
        case_id: UUID,
        action_id: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> VerifyOutcomeOutput:
        window_start, window_end = _aware(window_start), _aware(window_end)
        if window_end <= window_start:
            raise ValueError("Verification window end must be after start")
        case = self.session.get(models.Case, case_id)
        action = self.session.get(models.Action, action_id)
        if case is None:
            raise CaseWorkflowFailure("Case not found")
        if action is None or action.case_id != case_id:
            raise CaseWorkflowFailure("Action not found for case")
        if action.status != "completed":
            raise CaseWorkflowFailure("Action must complete before verification")
        report = ReportRepository(self.session).latest(case_id)
        if report is None:
            raise CaseWorkflowFailure("Case has no investigation report")
        existing = self._existing_verification(
            case=case,
            action=action,
            window_start=window_start,
            window_end=window_end,
        )
        if existing is not None:
            return existing
        meter_ids = list(
            self.session.scalars(
                select(models.CaseMeter.meter_id).where(models.CaseMeter.case_id == case_id)
            )
        )
        if not meter_ids:
            raise CaseWorkflowFailure("Case has no affected meters")
        anomaly_time = self.session.scalar(
            select(func.max(models.Anomaly.detected_at)).where(
                models.Anomaly.meter_id.in_(meter_ids)
            )
        )
        if anomaly_time is None:
            raise CaseWorkflowFailure("Case has no anomaly timestamp")
        anomaly_time = _aware(anomaly_time)
        run = self._run(
            case_id=case_id,
            event_type="verification.requested",
            payload={
                "case_id": str(case_id),
                "action_id": str(action_id),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
            },
        )
        try:
            output = self._verify_with_run(
                run=run,
                case=case,
                action=action,
                report=report,
                meter_ids=meter_ids,
                anomaly_time=anomaly_time,
                window_start=window_start,
                window_end=window_end,
            )
            run.status = "succeeded"
            run.ended_at = datetime.now(UTC)
            run.state_json = {
                "stage": "verification_completed",
                "verification_result": output.model_dump(mode="json"),
                "report_id": str(output.report_id),
                "replan_required": output.replan_required,
                "completed_tools": [
                    "calculate_baseline",
                    "validate_reading_quality",
                    "estimate_revenue_at_risk",
                    "verify_case_outcome",
                ],
            }
            self.session.flush()
            return output
        except Exception as exc:
            run.status = "failed"
            run.ended_at = datetime.now(UTC)
            run.state_json = {"stage": "failed", "error": type(exc).__name__}
            self.session.flush()
            raise

    def _verify_with_run(
        self,
        *,
        run: models.AgentRun,
        case: models.Case,
        action: models.Action,
        report: models.InvestigationReport,
        meter_ids: list[str],
        anomaly_time: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> VerifyOutcomeOutput:
        settings = get_settings()
        repository = ReadingRepository(self.session)
        target_meter = meter_ids[0]
        history_rows = repository.window(
            target_meter, anomaly_time - timedelta(days=35), anomaly_time, limit=10_000
        )
        post_rows = repository.window(target_meter, window_start, window_end, limit=10_000)
        history = [
            ReadingPoint(
                timestamp=_aware(row.timestamp), kwh=row.kwh, quality_flag=row.quality_flag
            )
            for row in history_rows
        ]
        post = [
            ReadingPoint(
                timestamp=_aware(row.timestamp), kwh=row.kwh, quality_flag=row.quality_flag
            )
            for row in post_rows
        ]
        baseline = self._execute(
            "calculate_baseline",
            BaselineInput(run_id=run.id, case_id=case.id, readings=history, cutoff=anomaly_time),
            BaselineOutput,
        )
        quality = self._execute(
            "validate_reading_quality",
            QualityInput(run_id=run.id, case_id=case.id, readings=post),
            QualityOutput,
        )
        profiles = {
            (item.weekday, item.hour, item.minute): item for item in baseline.profiles
        }
        comparisons: list[tuple[float, float, bool]] = []
        for point in post:
            if point.kwh is None or point.quality_flag != "valid":
                continue
            slot = (
                point.timestamp.weekday(),
                point.timestamp.hour,
                0 if point.timestamp.minute < 30 else 30,
            )
            profile = profiles.get(slot)
            if profile is None:
                continue
            spread = max(profile.iqr_kwh * 1.5, profile.median_kwh * 0.20, 0.05)
            lower, upper = profile.median_kwh - spread, profile.median_kwh + spread
            comparisons.append((point.kwh, profile.median_kwh, lower <= point.kwh <= upper))

        peer_agreements = self._peer_agreements(
            target_meter=target_meter,
            post=post,
            tolerance=settings.verification_peer_tolerance_fraction,
        )
        valid_count = len(comparisons)
        within_fraction = (
            sum(item[2] for item in comparisons) / valid_count if valid_count else None
        )
        deviations = [
            abs(observed - expected) / max(expected, 0.001)
            for observed, expected, _ in comparisons
        ]
        peer_fraction = (
            sum(peer_agreements) / len(peer_agreements) if peer_agreements else None
        )
        enough = valid_count >= settings.verification_minimum_observations
        baseline_pass = (
            within_fraction is not None
            and within_fraction >= settings.verification_baseline_pass_fraction
        )
        peer_pass = (
            peer_fraction is None
            or peer_fraction >= settings.verification_peer_pass_fraction
        )
        window_signal = ObservationWindowResult(
            status="ready" if enough and quality.reliable else "not_ready",
            expected_count=settings.verification_minimum_observations,
            usable_count=valid_count,
            reason=(
                "The aggregate observation policy has enough reliable readings."
                if enough and quality.reliable
                else "The aggregate observation policy lacks enough reliable readings."
            ),
        )
        baseline_signal = ReturnToBaselineResult(
            status="unknown" if not enough else "pass" if baseline_pass else "fail",
            reason=(
                "Baseline recovery could not be evaluated."
                if not enough
                else "The required fraction of observations returned to baseline."
                if baseline_pass
                else "Too few observations returned to baseline."
            ),
        )
        peer_signal = PeerAgreementResult(
            status=(
                "unknown"
                if peer_fraction is None
                else "pass"
                if peer_pass
                else "fail"
            ),
            reason=(
                "Peer evidence is unavailable."
                if peer_fraction is None
                else "The required fraction of observations agrees with peers."
                if peer_pass
                else "Too few observations agree with peers."
            ),
        )
        deterministic = summarize_verification(
            window=window_signal,
            baseline=baseline_signal,
            peer=peer_signal,
        )
        warnings = []
        if not enough:
            outcome = "insufficient_observations"
            warnings.append(
                "At least "
                f"{settings.verification_minimum_observations} valid observations are required."
            )
        elif peer_fraction is not None and baseline_pass != peer_pass:
            outcome = "ambiguous"
            warnings.append("Baseline and peer recovery signals disagree.")
        elif baseline_pass and peer_pass and quality.reliable:
            outcome = "recovered"
        else:
            outcome = "persistent"

        expected_kwh = sum(item[1] for item in comparisons) if comparisons else None
        observed_kwh = sum(item[0] for item in comparisons) if comparisons else None
        metrics = VerificationMetrics(
            status=outcome,
            window_start=window_start,
            window_end=window_end,
            observation_count=len(post),
            valid_observation_count=valid_count,
            expected_kwh=expected_kwh,
            observed_kwh=observed_kwh,
            within_baseline_fraction=within_fraction,
            peer_agreement_fraction=peer_fraction,
            median_deviation_fraction=median(deviations) if deviations else None,
            confidence=min(
                quality.quality_score / 100,
                valid_count / max(settings.verification_minimum_observations, 1),
            ),
            policy_version=settings.verification_policy_version,
            deterministic_status=deterministic.status,
            component_statuses={
                "window": deterministic.window_status,
                "baseline": deterministic.baseline_status,
                "peer": deterministic.peer_status,
            },
            deterministic_reason=deterministic.reason,
            warnings=warnings,
        )
        revenue = self._remaining_revenue(
            run_id=run.id,
            case_id=case.id,
            meter_id=target_meter,
            expected_kwh=expected_kwh,
            observed_kwh=observed_kwh,
            quality=quality,
            window_start=window_start,
            window_end=window_end,
        )
        return self._execute(
            "verify_case_outcome",
            VerifyOutcomeInput(
                run_id=run.id,
                case_id=case.id,
                target_case_id=case.id,
                action_id=action.id,
                source_report_id=report.id,
                metrics=metrics,
                remaining_revenue_risk=revenue,
            ),
            VerifyOutcomeOutput,
        )

    def _peer_agreements(
        self, *, target_meter: str, post: list[ReadingPoint], tolerance: float
    ) -> list[bool]:
        meter = self.session.get(models.Meter, target_meter)
        if meter is None or meter.transformer_id is None or not post:
            return []
        peer_ids = list(
            self.session.scalars(
                select(models.Meter.id).where(
                    models.Meter.transformer_id == meter.transformer_id,
                    models.Meter.id != target_meter,
                )
            )
        )
        if not peer_ids:
            return []
        timestamps = [_aware(item.timestamp) for item in post if item.kwh is not None]
        rows = list(
            self.session.scalars(
                select(models.Reading).where(
                    models.Reading.meter_id.in_(peer_ids),
                    models.Reading.timestamp.in_(timestamps),
                )
            )
        )
        by_time: dict[datetime, list[float]] = {}
        for row in rows:
            by_time.setdefault(_aware(row.timestamp), []).append(row.kwh)
        return [
            abs(float(point.kwh) - median(by_time[_aware(point.timestamp)]))
            / max(median(by_time[_aware(point.timestamp)]), 0.001)
            <= tolerance
            for point in post
            if point.kwh is not None and by_time.get(_aware(point.timestamp))
        ]

    def _remaining_revenue(
        self,
        *,
        run_id: UUID,
        case_id: UUID,
        meter_id: str,
        expected_kwh: float | None,
        observed_kwh: float | None,
        quality: QualityOutput,
        window_start: datetime,
        window_end: datetime,
    ) -> RevenueRiskOutput:
        meter = self.session.get(models.Meter, meter_id)
        tariffs = (
            TariffRepository(self.session).effective(meter.customer_segment, window_end)
            if meter is not None and meter.customer_segment
            else []
        )
        tariff = tariffs[0] if len(tariffs) == 1 else None
        return self._execute(
            "estimate_revenue_at_risk",
            RevenueRiskInput(
                run_id=run_id,
                case_id=case_id,
                expected_kwh=expected_kwh,
                observed_kwh=observed_kwh or 0,
                tariff_id=tariff.id if tariff else None,
                tariff_jod_per_kwh=float(tariff.jod_per_kwh) if tariff else None,
                tariff_version=tariff.name if tariff else None,
                tariff_source=tariff.source if tariff else None,
                tariff_name=tariff.name if tariff else None,
                data_reliable=quality.reliable,
                data_confidence=quality.quality_score / 100,
                quality_score=quality.quality_score,
                calculation_timestamp=window_end,
                window_start=window_start,
                window_end=window_end,
                forecast_reference="verification weekday/half-hour baseline",
                observed_reference=f"post-action-readings:{meter_id}",
                quality_reference=f"verification-quality:{run_id}",
            ),
            RevenueRiskOutput,
        )
