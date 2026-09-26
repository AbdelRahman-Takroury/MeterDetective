"""Day 3/4 event-to-case investigation workflow."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pandas as pd
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.common import EvidenceReference, RunStatus
from app.contracts.report import AnswerStatus, InvestigationAnswer
from app.contracts.tool import ToolInput, ToolOutput
from app.core.config import get_settings
from app.db import models
from app.db.repositories import (
    EventRepository,
    FinanceRepository,
    RankingRepository,
    ReadingRepository,
    ReportRepository,
    TariffRepository,
)
from app.tools.advanced import (
    CustomerDerInput,
    CustomerDerOutput,
    EnergyBalanceInput,
    EnergyBalanceOutput,
    ForecastInput,
    ForecastOutput,
    QueueCase,
    RevenueRiskInput,
    RevenueRiskOutput,
    TriageInput,
    TriageOutput,
    customer_der_tool,
    energy_balance_tool,
    forecast_tool,
    revenue_risk_tool,
    triage_tool,
)
from app.tools.advanced_analytics import analyze_weather_alignment
from app.tools.analytics import (
    AnomalyInput,
    AnomalyOutput,
    AssetNode,
    BaselineInput,
    BaselineOutput,
    ConnectedAssetsInput,
    ConnectedAssetsOutput,
    MeterConnection,
    MeterSeries,
    PeerComparisonInput,
    PeerComparisonOutput,
    PeerSelectionInput,
    PeerSelectionOutput,
    QualityInput,
    QualityOutput,
    ReadingPoint,
    SharedIncidentInput,
    SharedIncidentOutput,
    calculate_baseline,
    compare_with_peers,
    detect_anomaly,
    detect_shared_incident,
    get_connected_assets,
    select_dynamic_peers,
    validate_reading_quality,
)
from app.tools.database import (
    MeterProfileInput,
    MeterProfileOutput,
    PrecedentInput,
    PrecedentOutput,
    ReadingWindowInput,
    ReadingWindowOutput,
    find_meter_precedents,
    get_meter_profile,
    get_reading_window,
)
from app.tools.knowledge import (
    KnowledgeSearchInput,
    KnowledgeSearchOutput,
    KnowledgeSearchTool,
)
from app.tools.registry import ToolRegistry
from app.tools.weather import (
    WeatherClient,
    WeatherContextInput,
    WeatherContextOutput,
)


class InvestigationFailure(RuntimeError):
    """A required tool failed and the partial run was persisted safely."""


class ReplayCommand(ToolOutput):
    meter_id: str
    event_time: datetime
    lookback_days: int = Field(default=35, ge=7, le=90)
    idempotency_key: str | None = None


class ReplayResult(ToolOutput):
    event_id: UUID
    run_id: UUID
    case_id: UUID | None = None
    report_id: UUID | None = None
    status: str
    duplicate: bool = False
    anomaly_count: int = 0
    tool_calls: int = 0


class EvidenceDraft(ToolOutput):
    key: str
    kind: str
    source: str
    value: dict[str, Any]
    reliability: float = Field(ge=0, le=1)
    observed_at: datetime | None = None


class HypothesisDraft(ToolOutput):
    label: str
    confidence: float = Field(ge=0, le=1)
    support_keys: list[str] = Field(default_factory=list)
    contradiction_keys: list[str] = Field(default_factory=list)


class RecordInvestigationInput(ToolInput):
    target_case_id: UUID
    event_id: UUID
    meter_id: str
    anomaly_type: str
    severity: float = Field(ge=0, le=100)
    reliability: float = Field(ge=0, le=1)
    detected_at: datetime
    anomaly_features: dict[str, Any]
    evidence_drafts: list[EvidenceDraft]
    hypothesis_drafts: list[HypothesisDraft]
    peer_status: str
    incident_type: str
    precedent_case_ids: list[UUID]
    precedent_summary: str
    weather_status: str = "unavailable"
    weather_summary: dict[str, Any] = Field(default_factory=dict)
    knowledge_status: str = "no_results"
    knowledge_citations: list[dict[str, Any]] = Field(default_factory=list)
    weather_alignment: dict[str, Any] = Field(default_factory=dict)
    forecast: ForecastOutput
    energy_balance: EnergyBalanceOutput
    customer_der: CustomerDerOutput
    revenue_risk: RevenueRiskOutput
    triage: TriageOutput


class RecordInvestigationOutput(ToolOutput):
    case_id: UUID
    report_id: UUID
    report_version: int
    evidence_ids: list[UUID]
    hypothesis_ids: list[UUID]


def _answer(
    question_id: int,
    *,
    status: AnswerStatus,
    answer: str,
    confidence: float,
    evidence: list[EvidenceReference] | None = None,
    tools: list[str] | None = None,
    values: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
    fresh_as_of: datetime | None = None,
) -> InvestigationAnswer:
    return InvestigationAnswer(
        question_id=question_id,
        status=status,
        answer=answer,
        confidence=confidence,
        supporting_evidence=evidence or [],
        tools_used=tools or [],
        data_sources=sorted({item.source for item in evidence or []}),
        structured_values=values or {},
        limitations=limitations or [],
        fresh_as_of=fresh_as_of,
    )


def _recalculate_active_queue_history(
    session: Session, *, new_case_id: UUID, calculated_at: datetime
) -> None:
    """Version prior active reports when a newly inserted case changes their queue snapshot."""
    ranking = RankingRepository(session)
    reports = ReportRepository(session)
    finance = FinanceRepository(session)
    active = [case for case in ranking.active_cases(limit=500) if case.triage_score is not None]
    scores = [float(case.triage_score) for case in active]
    active_count = len(active)
    if active_count < 2:
        return

    for case in active:
        if case.id == new_case_id:
            continue
        score = float(case.triage_score)
        active_rank = 1 + sum(other > score for other in scores)
        percentile = round(
            100.0
            if active_count == 1
            else 100 * sum(other < score for other in scores) / (active_count - 1),
            2,
        )
        previous_report = reports.latest(case.id)
        if previous_report is None:
            continue
        previous_assessment = ranking.by_report(previous_report.id)
        if previous_assessment is None:
            continue
        if (
            previous_assessment.active_rank == active_rank
            and previous_assessment.active_count == active_count
            and float(previous_assessment.percentile) == percentile
        ):
            continue

        snapshot = models.Evidence(
            case_id=case.id,
            kind="triage_recalculation",
            source="database.active_cases",
            value_json={
                "triggering_case_id": str(new_case_id),
                "active_rank": active_rank,
                "active_count": active_count,
                "percentile": percentile,
                "policy_version": previous_assessment.policy_version,
                "calculated_at": calculated_at.isoformat(),
            },
            reliability=1,
        )
        session.add(snapshot)
        session.flush()

        answers = deepcopy(previous_report.answers_json)
        triage_answer = next(item for item in answers if item["question_id"] == 18)
        factors = deepcopy(previous_assessment.factors_json)
        factors["queue_recalculation"] = {
            "triggering_case_id": str(new_case_id),
            "calculated_at": calculated_at.isoformat(),
        }
        triage_answer.update(
            {
                "answer": (
                    f"Triage is {previous_assessment.band}, rank {active_rank} "
                    f"of {active_count}."
                ),
                "structured_values": {
                    "score": score,
                    "band": previous_assessment.band,
                    "active_rank": active_rank,
                    "active_count": active_count,
                    "percentile": percentile,
                    "factors": factors,
                    "policy_version": previous_assessment.policy_version,
                },
                "fresh_as_of": calculated_at.isoformat(),
            }
        )
        reference = EvidenceReference(
            evidence_id=str(snapshot.id),
            source=snapshot.source,
            kind=snapshot.kind,
            observed_at=calculated_at,
            reliability=1,
            metadata=snapshot.value_json,
        ).model_dump(mode="json")
        triage_answer["supporting_evidence"] = [
            *triage_answer.get("supporting_evidence", []),
            reference,
        ]
        triage_answer["data_sources"] = sorted(
            {*triage_answer.get("data_sources", []), snapshot.source}
        )

        previous_financial = finance.by_report(previous_report.id)
        report = reports.add_version(
            case_id=case.id,
            status=previous_report.status,
            answers=answers,
            completeness=previous_report.completeness,
        )
        if previous_financial is not None:
            finance.add(
                case_id=case.id,
                report_id=report.id,
                tariff_id=previous_financial.tariff_id,
                missing_kwh=(
                    previous_financial.missing_kwh_low,
                    previous_financial.missing_kwh_base,
                    previous_financial.missing_kwh_high,
                ),
                risk_jod=(
                    previous_financial.risk_jod_low,
                    previous_financial.risk_jod_base,
                    previous_financial.risk_jod_high,
                ),
                assumptions=deepcopy(previous_financial.assumptions_json),
                confidence=previous_financial.confidence,
            )
        ranking.add_assessment(
            case_id=case.id,
            report_id=report.id,
            score=score,
            band=previous_assessment.band,
            active_rank=active_rank,
            active_count=active_count,
            percentile=percentile,
            factors=factors,
            policy_version=previous_assessment.policy_version,
        )
        session.add(
            models.CaseEvent(
                case_id=case.id,
                event_type="triage_recalculated",
                details_json={
                    "triggering_case_id": str(new_case_id),
                    "report_id": str(report.id),
                    "report_version": report.version,
                    "active_rank": active_rank,
                    "active_count": active_count,
                    "percentile": percentile,
                },
            )
        )


def record_investigation(
    data: RecordInvestigationInput, session: Session
) -> RecordInvestigationOutput:
    event = session.get(models.Event, data.event_id)
    if event is None:
        raise ValueError("Trigger event not found")
    existing = session.scalar(
        select(models.Anomaly).where(
            models.Anomaly.event_id == data.event_id,
            models.Anomaly.meter_id == data.meter_id,
            models.Anomaly.type == data.anomaly_type,
        )
    )
    if existing is not None:
        raise ValueError("Investigation already recorded for this event and anomaly")

    case = models.Case(
        id=data.target_case_id,
        title=f"{data.anomaly_type.replace('_', ' ').title()} at {data.meter_id}",
        status="investigating",
        confidence=max((item.confidence for item in data.hypothesis_drafts), default=0),
    )
    session.add(case)
    session.flush()
    session.add(models.CaseMeter(case_id=case.id, meter_id=data.meter_id, relationship="affected"))
    session.add(
        models.Anomaly(
            meter_id=data.meter_id,
            event_id=data.event_id,
            type=data.anomaly_type,
            severity=data.severity,
            reliability_score=data.reliability,
            features_json=data.anomaly_features,
            detected_at=data.detected_at,
        )
    )

    persisted: dict[str, models.Evidence] = {}
    for draft in data.evidence_drafts:
        row = models.Evidence(
            case_id=case.id,
            kind=draft.kind,
            source=draft.source,
            value_json={
                **draft.value,
                "observed_at": draft.observed_at.isoformat() if draft.observed_at else None,
            },
            reliability=draft.reliability,
        )
        session.add(row)
        session.flush()
        persisted[draft.key] = row

    hypotheses = []
    for draft in data.hypothesis_drafts:
        support = [str(persisted[key].id) for key in draft.support_keys if key in persisted]
        contradiction = [
            str(persisted[key].id) for key in draft.contradiction_keys if key in persisted
        ]
        row = models.Hypothesis(
            case_id=case.id,
            label=draft.label,
            confidence=draft.confidence,
            support_json=support,
            contradiction_json=contradiction,
            update_history_json=[
                {
                    "reason": "initial deterministic evidence assessment",
                    "confidence": draft.confidence,
                    "supporting_evidence": support,
                    "contradicting_evidence": contradiction,
                    "at": data.detected_at.isoformat(),
                }
            ],
        )
        session.add(row)
        session.flush()
        hypotheses.append(row)
        session.add(
            models.CaseEvent(
                case_id=case.id,
                event_type="hypothesis_created",
                details_json={
                    "hypothesis_id": str(row.id),
                    "label": row.label,
                    "confidence": row.confidence,
                    "supporting_evidence": support,
                    "contradicting_evidence": contradiction,
                },
            )
        )

    def refs(*keys: str) -> list[EvidenceReference]:
        result = []
        for key in keys:
            row = persisted.get(key)
            if row is None:
                continue
            result.append(
                EvidenceReference(
                    evidence_id=str(row.id),
                    source=row.source,
                    kind=row.kind,
                    observed_at=data.detected_at,
                    reliability=row.reliability,
                    metadata=row.value_json,
                )
            )
        return result

    anomaly_refs = refs("anomaly")
    quality_refs = refs("quality")
    peer_refs = refs("peers", "shared")
    precedent_refs = refs("precedents")
    weather_refs = refs("weather")
    knowledge_refs = refs("knowledge")
    der_refs = refs("customer_der")
    energy_refs = refs("energy_balance")
    revenue_refs = refs("revenue_risk")
    triage_refs = refs("triage")
    leading = max(hypotheses, key=lambda item: item.confidence)
    answers = [
        _answer(
            1,
            status=AnswerStatus.ANSWERED,
            answer=f"A {data.anomaly_type} anomaly was detected for {data.meter_id}.",
            confidence=data.reliability,
            evidence=anomaly_refs,
            tools=["detect_anomaly"],
            values={"anomaly_type": data.anomaly_type},
            fresh_as_of=data.detected_at,
        ),
        _answer(
            2,
            status=AnswerStatus.ANSWERED,
            answer=f"The normalized severity is {data.severity:.2f} out of 100.",
            confidence=data.reliability,
            evidence=anomaly_refs,
            tools=["calculate_baseline", "detect_anomaly"],
            values={"severity": data.severity, "unit": "score_0_100"},
            fresh_as_of=data.detected_at,
        ),
        _answer(
            3,
            status=AnswerStatus.ANSWERED,
            answer=f"Reading reliability is {data.reliability:.2f}.",
            confidence=data.reliability,
            evidence=quality_refs,
            tools=["validate_reading_quality"],
            values={"reliability": data.reliability},
            fresh_as_of=data.detected_at,
        ),
    ]
    if data.peer_status == "answered" and peer_refs:
        answers.extend(
            [
                _answer(
                    4,
                    status=AnswerStatus.ANSWERED,
                    answer=f"The incident is classified as {data.incident_type}.",
                    confidence=leading.confidence,
                    evidence=peer_refs,
                    tools=["compare_with_peers", "detect_shared_incident"],
                    values={"incident_type": data.incident_type},
                    fresh_as_of=data.detected_at,
                ),
                _answer(
                    5,
                    status=AnswerStatus.ANSWERED,
                    answer=(
                        "Related meters show the same pattern."
                        if data.incident_type == "shared"
                        else "Related meters do not show a shared pattern."
                    ),
                    confidence=leading.confidence,
                    evidence=peer_refs,
                    tools=["select_dynamic_peers", "detect_shared_incident"],
                    values={"incident_type": data.incident_type},
                    fresh_as_of=data.detected_at,
                ),
            ]
        )
    else:
        for question_id in (4, 5):
            answers.append(
                _answer(
                    question_id,
                    status=AnswerStatus.UNKNOWN,
                    answer="Peer evidence is insufficient.",
                    confidence=0,
                    limitations=["No qualified peer group was available."],
                )
            )
    if (
        data.weather_status == "answered"
        and data.weather_alignment.get("status") == "success"
        and weather_refs
    ):
        answers.append(
            _answer(
                6,
                status=AnswerStatus.ANSWERED,
                answer=(
                    "Weather evidence is "
                    f"{data.weather_alignment.get('weather_evidence', 'unknown')}; "
                    "it is contextual evidence and does not prove causation."
                ),
                confidence=0.75,
                evidence=weather_refs,
                tools=["get_weather_context"],
                values={**data.weather_summary, **data.weather_alignment},
                limitations=["Weather context alone cannot establish the cause of a load change."],
                fresh_as_of=data.detected_at,
            )
        )
    else:
        answers.append(
            _answer(
                6,
                status=AnswerStatus.UNKNOWN,
                answer="Weather evidence is unavailable; no weather explanation was inferred.",
                confidence=0,
                tools=["get_weather_context"],
                limitations=["The external weather source failed or returned no observations."],
                fresh_as_of=data.detected_at,
            )
        )
    if data.customer_der.status == "answered" and der_refs:
        behavior = data.customer_der.customer_behavior
        answers.append(
            _answer(
                7,
                status=AnswerStatus.ANSWERED,
                answer=f"Customer-behavior evidence is {behavior.get('evidence', 'unknown')}.",
                confidence=0.7,
                evidence=der_refs,
                tools=["analyze_customer_der_context"],
                values={"customer_behavior": behavior},
                limitations=behavior.get("limitations", []),
                fresh_as_of=data.detected_at,
            )
        )
        answers.append(
            _answer(
                8,
                status=AnswerStatus.ANSWERED,
                answer=(
                    f"EV evidence is {data.customer_der.ev.get('evidence', 'unknown')}; "
                    f"solar evidence is {data.customer_der.solar.get('evidence', 'unknown')}."
                ),
                confidence=0.7,
                evidence=der_refs,
                tools=["analyze_customer_der_context"],
                values={"ev": data.customer_der.ev, "solar": data.customer_der.solar},
                limitations=[
                    "DER signatures and configured metadata are supporting evidence only."
                ],
                fresh_as_of=data.detected_at,
            )
        )
    else:
        for question_id in (7, 8):
            answers.append(
                _answer(
                    question_id,
                    status=AnswerStatus.UNKNOWN,
                    answer="Customer and DER evidence is unavailable.",
                    confidence=0,
                    limitations=[
                        data.customer_der.reason
                        or "Expected usage or customer metadata was unavailable."
                    ],
                    fresh_as_of=data.detected_at,
                )
            )
    if data.energy_balance.status == "answered" and energy_refs:
        answers.append(
            _answer(
                9,
                status=AnswerStatus.ANSWERED,
                answer=f"Transformer energy balance is {data.energy_balance.balance_status}.",
                confidence=data.energy_balance.confidence,
                evidence=energy_refs,
                tools=["calculate_energy_balance"],
                values=data.energy_balance.values,
                limitations=["Energy balance depends on interval coverage and configured losses."],
                fresh_as_of=data.detected_at,
            )
        )
    else:
        answers.append(
            _answer(
                9,
                status=AnswerStatus.UNKNOWN,
                answer="Transformer energy balance is unavailable.",
                confidence=0,
                limitations=[
                    data.energy_balance.reason
                    or "Transformer or downstream readings were unavailable."
                ],
                fresh_as_of=data.detected_at,
            )
        )
    hypothesis_refs = (
        refs(*data.hypothesis_drafts[0].support_keys) if data.hypothesis_drafts else []
    )
    synthesis_refs = hypothesis_refs or anomaly_refs
    if data.knowledge_status == "answered":
        synthesis_refs = [*synthesis_refs, *knowledge_refs]
    answers.extend(
        [
            _answer(
                10,
                status=AnswerStatus.ANSWERED,
                answer=f"The leading hypothesis is {leading.label}.",
                confidence=leading.confidence,
                evidence=synthesis_refs,
                tools=[
                    "detect_shared_incident",
                    "search_technical_knowledge",
                    "create_case_and_record_investigation",
                ],
                values={"hypothesis": leading.label},
                fresh_as_of=data.detected_at,
            ),
            _answer(
                11,
                status=AnswerStatus.ANSWERED,
                answer=f"Leading-hypothesis confidence is {leading.confidence:.2f}.",
                confidence=leading.confidence,
                evidence=synthesis_refs,
                tools=["create_case_and_record_investigation"],
                values={"confidence": leading.confidence},
                fresh_as_of=data.detected_at,
            ),
            _answer(
                12,
                status=AnswerStatus.ANSWERED,
                answer="Supporting and contradicting evidence are attached to each hypothesis.",
                confidence=leading.confidence,
                evidence=synthesis_refs,
                tools=["search_technical_knowledge", "create_case_and_record_investigation"],
                values={"citation_count": len(data.knowledge_citations)},
                limitations=(
                    []
                    if data.knowledge_status == "answered"
                    else ["No relevant stored technical document was retrieved."]
                ),
                fresh_as_of=data.detected_at,
            ),
        ]
    )
    for question_id, limitation in (
        (13, "Recommendation policy is scheduled after the Day 4 gate."),
        (14, "Approval policy is scheduled for Day 6."),
    ):
        answers.append(
            _answer(
                question_id,
                status=AnswerStatus.UNKNOWN,
                answer="No action decision has been made.",
                confidence=0,
                limitations=[limitation],
            )
        )
    answers.append(
        _answer(
            15,
            status=AnswerStatus.PENDING_VERIFICATION,
            answer="Outcome verification requires later readings or a completed action.",
            confidence=0,
            limitations=["No post-event verification window exists yet."],
        )
    )
    if data.revenue_risk.status == "answered" and revenue_refs:
        answers.append(
            _answer(
                16,
                status=AnswerStatus.ANSWERED,
                answer="Revenue at Risk was calculated deterministically in JOD.",
                confidence=data.revenue_risk.confidence,
                evidence=revenue_refs,
                tools=["estimate_revenue_at_risk"],
                values=data.revenue_risk.values,
                limitations=data.revenue_risk.values.get("assumptions", []),
                fresh_as_of=data.detected_at,
            )
        )
    else:
        answers.append(
            _answer(
                16,
                status=AnswerStatus.UNKNOWN,
                answer="Revenue at Risk is unknown.",
                confidence=0,
                limitations=[data.revenue_risk.reason or "No unique applicable tariff exists."],
                fresh_as_of=data.detected_at,
            )
        )
    answers.append(
        _answer(
            17,
            status=AnswerStatus.ANSWERED,
            answer=data.precedent_summary,
            confidence=1,
            evidence=precedent_refs,
            tools=["find_meter_precedents"],
            values={"precedent_case_ids": [str(item) for item in data.precedent_case_ids]},
            fresh_as_of=data.detected_at,
        )
    )
    answers.append(
        _answer(
            18,
            status=AnswerStatus.ANSWERED,
            answer=(
                f"Triage is {data.triage.band}, rank {data.triage.active_rank} "
                f"of {data.triage.active_count}."
            ),
            confidence=data.reliability,
            evidence=triage_refs,
            tools=["calculate_triage_priority"],
            values={
                "score": data.triage.score,
                "band": data.triage.band,
                "active_rank": data.triage.active_rank,
                "active_count": data.triage.active_count,
                "percentile": data.triage.percentile,
                "factors": data.triage.factors,
                "policy_version": data.triage.policy_version,
            },
            limitations=data.triage.warnings,
            fresh_as_of=data.detected_at,
        )
    )

    report = ReportRepository(session).add_version(
        case_id=case.id,
        status="investigating",
        answers=[
            item.model_dump(mode="json")
            for item in sorted(answers, key=lambda answer: answer.question_id)
        ],
        completeness=1,
    )
    if data.revenue_risk.status == "answered" and data.revenue_risk.tariff_id:
        values = data.revenue_risk.values
        missing = float(values["expected_missing_kwh"])
        risk = values["revenue_at_risk_jod"]
        FinanceRepository(session).add(
            case_id=case.id,
            report_id=report.id,
            tariff_id=data.revenue_risk.tariff_id,
            missing_kwh=(missing, missing, missing),
            risk_jod=(
                Decimal(str(risk["low"])),
                Decimal(str(risk["base"])),
                Decimal(str(risk["high"])),
            ),
            assumptions=[
                *values.get("assumptions", []),
                {"method_version": data.revenue_risk.method_version},
            ],
            confidence=data.revenue_risk.confidence,
        )
    RankingRepository(session).add_assessment(
        case_id=case.id,
        report_id=report.id,
        score=data.triage.score,
        band=data.triage.band,
        active_rank=data.triage.active_rank,
        active_count=data.triage.active_count,
        percentile=data.triage.percentile,
        factors=data.triage.factors,
        policy_version=data.triage.policy_version,
    )
    _recalculate_active_queue_history(
        session,
        new_case_id=case.id,
        calculated_at=datetime.now(UTC),
    )
    session.add(
        models.CaseEvent(
            case_id=case.id,
            event_type="investigation_recorded",
            details_json={
                "event_id": str(data.event_id),
                "report_id": str(report.id),
                "report_version": report.version,
                "leading_hypothesis": leading.label,
            },
        )
    )
    session.flush()
    return RecordInvestigationOutput(
        case_id=case.id,
        report_id=report.id,
        report_version=report.version,
        evidence_ids=[row.id for row in persisted.values()],
        hypothesis_ids=[row.id for row in hypotheses],
        evidence=[
            EvidenceReference(
                evidence_id=str(report.id),
                source="database.investigation_reports",
                kind="persisted_report",
                observed_at=data.detected_at,
                reliability=1,
            )
        ],
    )


def build_registry(
    *,
    weather_client: WeatherClient | None = None,
    knowledge_tool: KnowledgeSearchTool | None = None,
) -> ToolRegistry:
    settings = get_settings()
    weather_client = weather_client or WeatherClient(
        base_url=settings.weather_base_url,
        timeout_seconds=settings.weather_timeout_seconds,
        retry_count=settings.weather_retry_count,
        cache_ttl_seconds=settings.weather_cache_ttl_seconds,
    )
    knowledge_tool = knowledge_tool or KnowledgeSearchTool()
    registry = ToolRegistry()
    registry.register("get_meter_profile", get_meter_profile)
    registry.register("get_reading_window", get_reading_window)
    registry.register("validate_reading_quality", lambda data, _: validate_reading_quality(data))
    registry.register("calculate_baseline", lambda data, _: calculate_baseline(data))
    registry.register("detect_anomaly", lambda data, _: detect_anomaly(data))
    registry.register("forecast_expected_usage", lambda data, _: forecast_tool(data))
    registry.register("select_dynamic_peers", lambda data, _: select_dynamic_peers(data))
    registry.register("compare_with_peers", lambda data, _: compare_with_peers(data))
    registry.register("get_connected_assets", lambda data, _: get_connected_assets(data))
    registry.register("detect_shared_incident", lambda data, _: detect_shared_incident(data))
    registry.register("calculate_energy_balance", lambda data, _: energy_balance_tool(data))
    registry.register("find_meter_precedents", find_meter_precedents)
    registry.register("get_weather_context", lambda data, _: weather_client.get(data))
    registry.register("analyze_customer_der_context", lambda data, _: customer_der_tool(data))
    registry.register("search_technical_knowledge", knowledge_tool)
    registry.register("estimate_revenue_at_risk", lambda data, _: revenue_risk_tool(data))
    registry.register("calculate_triage_priority", lambda data, _: triage_tool(data))
    registry.register("create_case_and_record_investigation", record_investigation)
    return registry


class InvestigationService:
    def __init__(self, session: Session, registry: ToolRegistry | None = None) -> None:
        self.session = session
        self.registry = registry or build_registry()

    def _required(self, name: str, tool_input: ToolInput, output_type: type[ToolOutput]):
        execution = self.registry.execute(name, tool_input, self.session)
        if execution.status is not RunStatus.SUCCEEDED or execution.output is None:
            raise InvestigationFailure(execution.error.message if execution.error else name)
        return cast(Any, output_type.model_validate(execution.output.model_dump()))

    def _existing_result(self, event: models.Event) -> ReplayResult | None:
        statement = select(models.AgentRun).order_by(models.AgentRun.started_at)
        for run in self.session.scalars(statement):
            if run.trigger.get("event_id") != str(event.id):
                continue
            report_id = run.state_json.get("report_id")
            tool_calls = self.session.scalar(
                select(func.count())
                .select_from(models.ToolExecution)
                .where(models.ToolExecution.run_id == run.id)
            )
            return ReplayResult(
                event_id=event.id,
                run_id=run.id,
                case_id=run.case_id,
                report_id=UUID(report_id) if report_id else None,
                status=run.status,
                duplicate=True,
                tool_calls=int(tool_calls or 0),
            )
        return None

    def replay(self, command: ReplayCommand) -> ReplayResult:
        event_time = command.event_time.astimezone(UTC)
        key = command.idempotency_key or f"replay:{command.meter_id}:{event_time.isoformat()}"
        payload = {"meter_id": command.meter_id, "event_time": event_time.isoformat()}
        existing_event = EventRepository(self.session).get(key)
        if existing_event is not None:
            existing_result = self._existing_result(existing_event)
            if existing_result is not None:
                return existing_result
        event = EventRepository(self.session).add(
            idempotency_key=key,
            event_type="reading.received",
            payload=payload,
            status="processing",
        )
        run = models.AgentRun(
            trigger={"event_id": str(event.id), **payload},
            status="running",
            state_json={"stage": "started", "completed_tools": []},
        )
        self.session.add(run)
        self.session.flush()

        try:
            profile = self._required(
                "get_meter_profile",
                MeterProfileInput(run_id=run.id, meter_id=command.meter_id),
                MeterProfileOutput,
            )
            start = event_time - timedelta(days=command.lookback_days)
            end = event_time + timedelta(minutes=30)
            window = self._required(
                "get_reading_window",
                ReadingWindowInput(run_id=run.id, meter_id=command.meter_id, start=start, end=end),
                ReadingWindowOutput,
            )
            quality = self._required(
                "validate_reading_quality",
                QualityInput(run_id=run.id, readings=window.readings),
                QualityOutput,
            )
            baseline = self._required(
                "calculate_baseline",
                BaselineInput(run_id=run.id, readings=window.readings, cutoff=event_time),
                BaselineOutput,
            )
            event_readings = [item for item in window.readings if item.timestamp == event_time]
            anomaly = self._required(
                "detect_anomaly",
                AnomalyInput(
                    run_id=run.id,
                    readings=event_readings,
                    baseline_profiles=baseline.profiles,
                ),
                AnomalyOutput,
            )
            if anomaly.status != "anomalies_detected":
                event.status = "completed"
                run.status = "succeeded"
                run.ended_at = datetime.now(UTC)
                run.state_json = {
                    "stage": "normal_stop",
                    "anomaly_status": anomaly.status,
                    "completed_tools": 5,
                }
                self.session.flush()
                return ReplayResult(
                    event_id=event.id,
                    run_id=run.id,
                    status="normal",
                    anomaly_count=0,
                    tool_calls=5,
                )

            selected_event = max(anomaly.events, key=lambda item: item.severity)
            forecast = self._required(
                "forecast_expected_usage",
                ForecastInput(
                    run_id=run.id,
                    meter_id=command.meter_id,
                    target_timestamp=event_time,
                    readings=window.readings,
                ),
                ForecastOutput,
            )
            target_series = MeterSeries(
                meter_id=command.meter_id,
                customer_segment=profile.customer_segment,
                has_solar=profile.has_solar,
                has_ev=profile.has_ev,
                readings=window.readings,
            )
            candidates = self._candidate_series(profile, start, end)
            peer_selection = self._required(
                "select_dynamic_peers",
                PeerSelectionInput(
                    run_id=run.id,
                    target=target_series,
                    candidates=candidates,
                    event_time=event_time,
                ),
                PeerSelectionOutput,
            )
            selected_ids = {item.meter_id for item in peer_selection.peers}
            selected_series = [item for item in candidates if item.meter_id in selected_ids]
            comparison = self._required(
                "compare_with_peers",
                PeerComparisonInput(
                    run_id=run.id,
                    target=target_series,
                    peers=selected_series,
                    event_time=event_time,
                ),
                PeerComparisonOutput,
            )
            topology = self._required(
                "get_connected_assets",
                self._topology_input(run.id, command.meter_id),
                ConnectedAssetsOutput,
            )
            connected_ids = set(topology.connected_meter_ids)
            connected_series = [item for item in candidates if item.meter_id in connected_ids]
            shared = self._required(
                "detect_shared_incident",
                SharedIncidentInput(
                    run_id=run.id,
                    connected_series=connected_series,
                    event_time=event_time,
                ),
                SharedIncidentOutput,
            )
            energy_balance = self._required(
                "calculate_energy_balance",
                self._energy_balance_input(
                    run.id, profile, event_time, target_series, candidates
                ),
                EnergyBalanceOutput,
            )
            settings = get_settings()
            weather = self._required(
                "get_weather_context",
                WeatherContextInput(
                    run_id=run.id,
                    latitude=settings.demo_latitude,
                    longitude=settings.demo_longitude,
                    start=event_time - timedelta(hours=6),
                    end=event_time + timedelta(hours=6),
                ),
                WeatherContextOutput,
            )
            weather_alignment = self._weather_alignment(
                weather,
                event_time=event_time,
                actual_kwh=selected_event.observed_kwh,
                expected_kwh=forecast.expected_kwh,
            )
            customer_der = self._required(
                "analyze_customer_der_context",
                CustomerDerInput(
                    run_id=run.id,
                    meter_id=command.meter_id,
                    target_timestamp=event_time,
                    actual_kwh=selected_event.observed_kwh or 0,
                    expected_kwh=(
                        forecast.expected_kwh
                        if selected_event.observed_kwh is not None
                        else None
                    ),
                    readings=window.readings,
                    has_ev=profile.has_ev,
                    has_solar=profile.has_solar,
                    metadata_source=profile.metadata_source,
                ),
                CustomerDerOutput,
            )
            knowledge = self._required(
                "search_technical_knowledge",
                KnowledgeSearchInput(
                    run_id=run.id,
                    query=f"smart meter {selected_event.anomaly_type} {shared.incident_type}",
                ),
                KnowledgeSearchOutput,
            )
            precedents = self._required(
                "find_meter_precedents",
                PrecedentInput(run_id=run.id, meter_id=command.meter_id),
                PrecedentOutput,
            )
            tariffs = (
                TariffRepository(self.session).effective(profile.customer_segment, event_time)
                if profile.customer_segment
                else []
            )
            tariff = tariffs[0] if len(tariffs) == 1 else None
            revenue = self._required(
                "estimate_revenue_at_risk",
                RevenueRiskInput(
                    run_id=run.id,
                    expected_kwh=forecast.expected_kwh,
                    observed_kwh=selected_event.observed_kwh or 0,
                    tariff_id=tariff.id if tariff else None,
                    tariff_jod_per_kwh=float(tariff.jod_per_kwh) if tariff else None,
                    tariff_version=tariff.name if tariff else None,
                    tariff_source=tariff.source if tariff else None,
                    tariff_name=tariff.name if tariff else None,
                    data_reliable=(quality.reliable and selected_event.observed_kwh is not None),
                    quality_score=quality.quality_score,
                    calculation_timestamp=event_time,
                    window_start=event_time,
                    window_end=event_time + timedelta(minutes=30),
                    forecast_reference=forecast.method_version,
                    observed_reference=f"reading:{command.meter_id}:{event_time.isoformat()}",
                    quality_reference=f"tool-run:{run.id}:validate_reading_quality",
                ),
                RevenueRiskOutput,
            )
            provisional_case_id = uuid4()
            active_queue = [
                QueueCase(case_id=str(item.id), priority_score=item.triage_score)
                for item in RankingRepository(self.session).active_cases()
                if item.triage_score is not None
            ]
            triage = self._required(
                "calculate_triage_priority",
                TriageInput(
                    run_id=run.id,
                    target_case_id=provisional_case_id,
                    technical_severity=selected_event.severity,
                    scope_ratio=shared.affected_fraction or 0,
                    revenue_at_risk_jod=(
                        revenue.values.get("revenue_at_risk_jod", {}).get("base")
                        if revenue.status == "answered"
                        else None
                    ),
                    recurrence_score=min(
                        1,
                        (
                            len(precedents.exact_meter_cases)
                            + len(precedents.similar_system_cases)
                        )
                        / 3,
                    ),
                    upstream_evidence_score=(
                        energy_balance.confidence
                        if energy_balance.balance_status == "imbalanced"
                        else 0
                    ),
                    data_confidence=quality.quality_score / 100,
                    active_queue=active_queue,
                ),
                TriageOutput,
            )
            evidence_drafts = self._evidence_drafts(
                event_time,
                quality,
                selected_event,
                comparison,
                shared,
                precedents,
                weather,
                knowledge,
                forecast,
                energy_balance,
                customer_der,
                revenue,
                triage,
            )
            hypotheses = self._hypotheses(quality, shared)
            precedent_ids = [item.case_id for item in precedents.exact_meter_cases]
            precedent_ids.extend(item.case_id for item in precedents.similar_system_cases)
            recorded = self._required(
                "create_case_and_record_investigation",
                RecordInvestigationInput(
                    run_id=run.id,
                    target_case_id=provisional_case_id,
                    event_id=event.id,
                    meter_id=command.meter_id,
                    anomaly_type=selected_event.anomaly_type,
                    severity=selected_event.severity,
                    reliability=quality.quality_score / 100,
                    detected_at=event_time,
                    anomaly_features=selected_event.model_dump(mode="json"),
                    evidence_drafts=evidence_drafts,
                    hypothesis_drafts=hypotheses,
                    peer_status=shared.status,
                    incident_type=shared.incident_type,
                    precedent_case_ids=precedent_ids,
                    precedent_summary=precedents.pattern_summary,
                    weather_status=weather.status,
                    weather_summary={
                        "temperature_c_min": weather.temperature_c_min,
                        "temperature_c_max": weather.temperature_c_max,
                        "temperature_c_mean": weather.temperature_c_mean,
                    },
                    knowledge_status=knowledge.status,
                    knowledge_citations=[
                        item.model_dump(mode="json") for item in knowledge.citations
                    ],
                    weather_alignment=weather_alignment,
                    forecast=forecast,
                    energy_balance=energy_balance,
                    customer_der=customer_der,
                    revenue_risk=revenue,
                    triage=triage,
                ),
                RecordInvestigationOutput,
            )
            run.case_id = recorded.case_id
            run.status = "succeeded"
            run.ended_at = datetime.now(UTC)
            run.state_json = {
                "stage": "investigation_recorded",
                "case_id": str(recorded.case_id),
                "report_id": str(recorded.report_id),
                "incident_type": shared.incident_type,
                "completed_tools": 18,
            }
            event.status = "completed"
            self.session.flush()
            return ReplayResult(
                event_id=event.id,
                run_id=run.id,
                case_id=recorded.case_id,
                report_id=recorded.report_id,
                status="case_created",
                anomaly_count=len(anomaly.events),
                tool_calls=18,
            )
        except Exception as exc:
            run.status = "failed"
            run.ended_at = datetime.now(UTC)
            run.state_json = {"stage": "failed", "error": type(exc).__name__}
            event.status = "failed"
            self.session.flush()
            raise

    def _energy_balance_input(
        self,
        run_id: UUID,
        profile: MeterProfileOutput,
        event_time: datetime,
        target: MeterSeries,
        candidates: list[MeterSeries],
    ) -> EnergyBalanceInput:
        transformer_kwh = None
        if profile.transformer_id is not None:
            rows = ReadingRepository(self.session).transformer_window(
                profile.transformer_id,
                event_time,
                event_time + timedelta(minutes=30),
                limit=2,
            )
            if len(rows) == 1:
                transformer_kwh = rows[0].input_kwh
        downstream = []
        for series in [target, *candidates]:
            matches = [
                point.kwh
                for point in series.readings
                if point.timestamp == event_time and point.kwh is not None
            ]
            if len(matches) == 1:
                downstream.append(matches[0])
        return EnergyBalanceInput(
            run_id=run_id,
            transformer_id=profile.transformer_id,
            event_time=event_time,
            transformer_kwh=transformer_kwh,
            downstream_kwh=downstream,
        )

    @staticmethod
    def _weather_alignment(
        weather: WeatherContextOutput,
        *,
        event_time: datetime,
        actual_kwh: float | None,
        expected_kwh: float | None,
    ) -> dict[str, Any]:
        if weather.status != "answered" or actual_kwh is None or expected_kwh is None:
            return {
                "status": "unknown",
                "reason": "Weather observations or expected/actual usage are unavailable.",
            }
        frame = pd.DataFrame(
            {
                "DateTime": [item.timestamp for item in weather.observations],
                "temperature_c": [item.temperature_c for item in weather.observations],
            }
        )
        return cast(
            dict[str, Any],
            analyze_weather_alignment(
                frame,
                event_time,
                actual_kwh,
                expected_kwh,
                weather_mode="online",
            ),
        )

    def _candidate_series(
        self, profile: MeterProfileOutput, start: datetime, end: datetime
    ) -> list[MeterSeries]:
        if profile.transformer_id is None:
            return []
        meters = list(
            self.session.scalars(
                select(models.Meter)
                .where(
                    models.Meter.transformer_id == profile.transformer_id,
                    models.Meter.id != profile.meter_id,
                )
                .order_by(models.Meter.id)
            )
        )
        repository = ReadingRepository(self.session)
        result = []
        for meter in meters:
            rows = repository.window(meter.id, start, end, limit=10_000)
            result.append(
                MeterSeries(
                    meter_id=meter.id,
                    customer_segment=meter.customer_segment,
                    has_solar=meter.has_solar,
                    has_ev=meter.has_ev,
                    readings=[
                        ReadingPoint(
                            timestamp=(
                                row.timestamp.replace(tzinfo=UTC)
                                if row.timestamp.tzinfo is None
                                else row.timestamp.astimezone(UTC)
                            ),
                            kwh=row.kwh,
                            quality_flag=row.quality_flag,
                        )
                        for row in rows
                    ],
                )
            )
        return result

    def _topology_input(self, run_id: UUID, meter_id: str) -> ConnectedAssetsInput:
        assets = [
            AssetNode(
                asset_id=str(row.id),
                asset_type=row.asset_type,
                parent_id=str(row.parent_id) if row.parent_id else None,
            )
            for row in self.session.scalars(select(models.Asset).order_by(models.Asset.name))
            if row.asset_type in {"substation", "feeder", "transformer"}
        ]
        meters = [
            MeterConnection(meter_id=row.id, transformer_id=str(row.transformer_id))
            for row in self.session.scalars(select(models.Meter).order_by(models.Meter.id))
            if row.transformer_id is not None
        ]
        return ConnectedAssetsInput(
            run_id=run_id, target_meter_id=meter_id, assets=assets, meters=meters
        )

    @staticmethod
    def _evidence_drafts(
        event_time: datetime,
        quality: QualityOutput,
        anomaly: Any,
        comparison: PeerComparisonOutput,
        shared: SharedIncidentOutput,
        precedents: PrecedentOutput,
        weather: WeatherContextOutput,
        knowledge: KnowledgeSearchOutput,
        forecast: ForecastOutput,
        energy_balance: EnergyBalanceOutput,
        customer_der: CustomerDerOutput,
        revenue: RevenueRiskOutput,
        triage: TriageOutput,
    ) -> list[EvidenceDraft]:
        return [
            EvidenceDraft(
                key="quality",
                kind="data_quality",
                source="validate_reading_quality",
                value=quality.model_dump(mode="json", exclude={"evidence"}),
                reliability=1,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="anomaly",
                kind="anomaly",
                source="detect_anomaly",
                value=anomaly.model_dump(mode="json", exclude={"evidence"}),
                reliability=quality.quality_score / 100,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="peers",
                kind="peer_comparison",
                source="compare_with_peers",
                value=comparison.model_dump(mode="json", exclude={"evidence"}),
                reliability=0.8 if comparison.status == "answered" else 0.3,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="shared",
                kind="shared_incident",
                source="detect_shared_incident",
                value=shared.model_dump(mode="json", exclude={"evidence"}),
                reliability=shared.confidence,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="precedents",
                kind="precedent_search",
                source="find_meter_precedents",
                value=precedents.model_dump(mode="json", exclude={"evidence"}),
                reliability=1,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="weather",
                kind="weather_context",
                source="open-meteo",
                value=weather.model_dump(mode="json", exclude={"evidence"}),
                reliability=weather.confidence,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="knowledge",
                kind="technical_knowledge",
                source="database.document_chunks",
                value=knowledge.model_dump(mode="json", exclude={"evidence"}),
                reliability=0.85 if knowledge.status == "answered" else 0,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="forecast",
                kind="expected_usage_forecast",
                source="forecast_expected_usage",
                value=forecast.model_dump(mode="json", exclude={"evidence"}),
                reliability=forecast.evidence[0].reliability if forecast.evidence else 0,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="energy_balance",
                kind="energy_balance",
                source="calculate_energy_balance",
                value=energy_balance.model_dump(mode="json", exclude={"evidence"}),
                reliability=energy_balance.confidence,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="customer_der",
                kind="customer_der_context",
                source="analyze_customer_der_context",
                value=customer_der.model_dump(mode="json", exclude={"evidence"}),
                reliability=0.7 if customer_der.status == "answered" else 0,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="revenue_risk",
                kind="financial_impact",
                source="estimate_revenue_at_risk",
                value=revenue.model_dump(mode="json", exclude={"evidence"}),
                reliability=revenue.confidence,
                observed_at=event_time,
            ),
            EvidenceDraft(
                key="triage",
                kind="triage_priority",
                source="calculate_triage_priority",
                value=triage.model_dump(mode="json", exclude={"evidence"}),
                reliability=quality.quality_score / 100,
                observed_at=event_time,
            ),
        ]

    @staticmethod
    def _hypotheses(quality: QualityOutput, shared: SharedIncidentOutput) -> list[HypothesisDraft]:
        data_failure = HypothesisDraft(
            label="communication_or_data_quality_failure",
            confidence=0.65 if not quality.reliable else 0.1,
            support_keys=["quality"] if not quality.reliable else [],
            contradiction_keys=[] if not quality.reliable else ["quality"],
        )
        if shared.status == "answered" and shared.incident_type == "shared":
            return [
                HypothesisDraft(
                    label="shared_upstream_or_transformer_issue",
                    confidence=max(0.7, shared.confidence),
                    support_keys=["shared", "peers"],
                ),
                HypothesisDraft(
                    label="individual_meter_malfunction",
                    confidence=0.2,
                    support_keys=["anomaly"],
                    contradiction_keys=["shared"],
                ),
                data_failure,
            ]
        return [
            HypothesisDraft(
                label="individual_meter_malfunction",
                confidence=0.7,
                support_keys=["anomaly", "peers"],
                contradiction_keys=["shared"] if shared.status == "answered" else [],
            ),
            HypothesisDraft(
                label="shared_upstream_or_transformer_issue",
                confidence=0.15,
                support_keys=[],
                contradiction_keys=["shared"] if shared.status == "answered" else [],
            ),
            data_failure,
        ]
