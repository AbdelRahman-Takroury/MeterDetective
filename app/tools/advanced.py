"""Typed Day 5 tools wrapping the pure dataframe-based analytics library."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import pandas as pd
from pydantic import Field

from app.contracts.common import EvidenceReference
from app.contracts.tool import ToolInput, ToolOutput
from app.tools.advanced_analytics import (
    adapt_day3_anomaly_event_for_severity,
    adapt_day3_quality_for_day5,
    adapt_day4_peer_comparison_for_severity,
    adapt_day4_shared_incident_for_triage,
    adapt_energy_balance_for_triage,
    analyze_customer_der_context,
    calculate_anomaly_severity,
    calculate_energy_balance,
    calculate_triage_priority,
    detect_isolation_forest_anomaly,
    estimate_revenue_at_risk,
    forecast_expected_usage,
)
from app.tools.analytics import (
    AnomalyEvent,
    PeerComparisonOutput,
    QualityOutput,
    ReadingPoint,
    SharedIncidentOutput,
)

READING_COLUMN = "KWH/hh (per half hour)"


def _frame(meter_id: str, readings: list[ReadingPoint]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Meter_ID": [meter_id for _ in readings],
            "DateTime": [item.timestamp for item in readings],
            READING_COLUMN: [item.kwh for item in readings],
        }
    )


def _unknown(reason: str, *, source: str, kind: str) -> dict[str, Any]:
    return {
        "status": "unknown",
        "reason": reason,
        "evidence": [
            EvidenceReference(source=source, kind=kind, reliability=0).model_dump(mode="json")
        ],
        "warnings": [reason],
    }


class AnalyticsEvidenceOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    values: dict[str, Any]
    reason: str | None = None


def _analytics_output(result: dict[str, Any]) -> AnalyticsEvidenceOutput:
    # Invalid or unavailable evidence must not become a numeric fallback.
    answered = result["status"] == "success"
    reason = None if answered else result["reason"]
    return AnalyticsEvidenceOutput(
        status="answered" if answered else "unknown",
        values=result,
        reason=reason,
        warnings=[reason] if reason else [],
    )


def quality_for_day5(data: QualityOutput) -> AnalyticsEvidenceOutput:
    return _analytics_output(adapt_day3_quality_for_day5(
        quality_score=data.quality_score,
        reliable=data.reliable,
        quality_status=data.status,
    ))


def shared_scope_for_triage(data: SharedIncidentOutput) -> AnalyticsEvidenceOutput:
    return _analytics_output(adapt_day4_shared_incident_for_triage(
        shared_status=data.status,
        affected_fraction=data.affected_fraction,
        incident_type=data.incident_type,
        shared_confidence=data.confidence,
    ))


class IsolationInput(ToolInput):
    meter_id: str
    target_timestamp: datetime
    readings: list[ReadingPoint]
    contamination: float = Field(default=0.05, gt=0, le=0.5)
    random_state: int = 42


def isolation_tool(data: IsolationInput) -> AnalyticsEvidenceOutput:
    return _analytics_output(detect_isolation_forest_anomaly(
        _frame(data.meter_id, data.readings),
        data.meter_id,
        data.target_timestamp,
        contamination=data.contamination,
        random_state=data.random_state,
    ))


class HybridSeverityInput(ToolInput):
    anomaly: AnomalyEvent
    comparison: PeerComparisonOutput
    isolation: AnalyticsEvidenceOutput


def hybrid_severity_tool(data: HybridSeverityInput) -> AnalyticsEvidenceOutput:
    day3 = adapt_day3_anomaly_event_for_severity(
        anomaly_type=data.anomaly.anomaly_type,
        deviation_pct=data.anomaly.components.get("deviation_pct"),
        event_severity=data.anomaly.severity,
    )
    day4 = adapt_day4_peer_comparison_for_severity(
        data.comparison.deviation_pct if data.comparison.status == "answered" else None
    )
    result = calculate_anomaly_severity(
        usage_deviation_fraction=day3.get("usage_deviation_fraction"),
        rule_based_score=day3.get("rule_based_score"),
        isolation_score=(
            data.isolation.values.get("isolation_score")
            if data.isolation.status == "answered" else None
        ),
        peer_deviation_score=day4.get("peer_deviation_score"),
    )
    result["raw_day3_severity"] = data.anomaly.severity
    result["day3_adapter"] = day3
    result["day4_adapter"] = day4
    return _analytics_output(result)


class ForecastInput(ToolInput):
    meter_id: str
    target_timestamp: datetime
    readings: list[ReadingPoint]
    min_samples: int = Field(default=3, ge=1, le=100)


class ForecastOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    expected_kwh: float | None = Field(default=None, ge=0)
    method: str | None = None
    support_count: int = Field(default=0, ge=0)
    method_version: str | None = None
    reason: str | None = None


def forecast_tool(data: ForecastInput) -> ForecastOutput:
    result = forecast_expected_usage(
        _frame(data.meter_id, data.readings),
        data.meter_id,
        data.target_timestamp,
        data.min_samples,
    )
    if result["status"] == "error":
        raise ValueError(result["reason"])
    if result["status"] == "unknown":
        return ForecastOutput(
            status="unknown", reason=result["reason"], warnings=[result["reason"]]
        )
    return ForecastOutput(
        status="answered",
        expected_kwh=result["expected_kwh"],
        method=result["method"],
        support_count=result["support_count"],
        method_version=result["method_version"],
        evidence=[
            EvidenceReference(
                source="forecast_expected_usage",
                kind="seasonal_forecast",
                observed_at=data.target_timestamp,
                reliability=min(1, result["support_count"] / max(data.min_samples, 1)),
                metadata={
                    "meter_id": data.meter_id,
                    "method": result["method"],
                    "method_version": result["method_version"],
                },
            )
        ],
    )


class EnergyBalanceInput(ToolInput):
    transformer_id: UUID | None = None
    event_time: datetime
    transformer_kwh: float | None = Field(default=None, ge=0)
    downstream_kwh: list[float] = Field(default_factory=list)
    technical_loss_rate: float = Field(default=0.03, ge=0, le=1)
    tolerance_pct: float = Field(default=0.05, ge=0, le=1)


class EnergyBalanceOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    balance_status: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    method_version: str | None = None
    reason: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    triage_adapter: dict[str, Any] = Field(default_factory=dict)


def energy_balance_tool(data: EnergyBalanceInput) -> EnergyBalanceOutput:
    if data.transformer_kwh is None or not data.downstream_kwh:
        reason = "Transformer or downstream interval readings are unavailable."
        return EnergyBalanceOutput(
            status="unknown", reason=reason, warnings=[reason],
            triage_adapter=adapt_energy_balance_for_triage({"status": "unknown"}),
        )
    result = calculate_energy_balance(
        data.transformer_kwh,
        data.downstream_kwh,
        technical_loss_rate=data.technical_loss_rate,
        tolerance_pct=data.tolerance_pct,
    )
    if result["status"] == "error":
        raise ValueError(result["reason"])
    confidence = min(1.0, len(data.downstream_kwh) / 3)
    return EnergyBalanceOutput(
        status="answered",
        balance_status=result["balance_status"],
        triage_adapter=adapt_energy_balance_for_triage(result),
        values={key: value for key, value in result.items() if key != "status"},
        method_version=result["method_version"],
        confidence=confidence,
        evidence=[
            EvidenceReference(
                source="database.transformer_readings",
                kind="energy_balance",
                observed_at=data.event_time,
                reliability=confidence,
                metadata={
                    "transformer_id": str(data.transformer_id),
                    "downstream_meter_count": len(data.downstream_kwh),
                    "method_version": result["method_version"],
                },
            )
        ],
    )


class CustomerDerInput(ToolInput):
    meter_id: str
    target_timestamp: datetime
    actual_kwh: float = Field(ge=0)
    expected_kwh: float | None = Field(default=None, ge=0)
    readings: list[ReadingPoint]
    has_ev: bool | None = None
    has_solar: bool | None = None
    metadata_source: str | None = None


class CustomerDerOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    customer_behavior: dict[str, Any] = Field(default_factory=dict)
    ev: dict[str, Any] = Field(default_factory=dict)
    solar: dict[str, Any] = Field(default_factory=dict)
    persistence: dict[str, Any] = Field(default_factory=dict)
    method_version: str | None = None
    reason: str | None = None


def customer_der_tool(data: CustomerDerInput) -> CustomerDerOutput:
    if data.expected_kwh is None:
        reason = "Expected usage is unavailable for behavior and DER analysis."
        return CustomerDerOutput(status="unknown", reason=reason, warnings=[reason])
    metadata = pd.DataFrame(
        [
            {
                "Meter_ID": data.meter_id,
                "Has_EV": data.has_ev,
                "Has_Solar": data.has_solar,
            }
        ]
    )
    result = analyze_customer_der_context(
        _frame(data.meter_id, data.readings),
        metadata,
        data.meter_id,
        data.target_timestamp,
        data.actual_kwh,
        data.expected_kwh,
    )
    if result["status"] == "error":
        raise ValueError(result["reason"])
    if result["status"] == "unknown":
        return CustomerDerOutput(
            status="unknown", reason=result["reason"], warnings=[result["reason"]]
        )
    return CustomerDerOutput(
        status="answered",
        customer_behavior=result["customer_behavior"],
        ev=result["ev"],
        solar=result["solar"],
        persistence=result["persistence"],
        method_version=result["method_version"],
        evidence=[
            EvidenceReference(
                source=data.metadata_source or "meter_metadata_unknown",
                kind="customer_der_context",
                observed_at=data.target_timestamp,
                reliability=0.7 if data.metadata_source else 0.4,
                metadata={
                    "meter_id": data.meter_id,
                    "metadata_is_synthetic": bool(
                        data.metadata_source and "synthetic" in data.metadata_source.lower()
                    ),
                    "method_version": result["method_version"],
                },
            )
        ],
    )


class RevenueRiskInput(ToolInput):
    expected_kwh: float | None = Field(default=None, ge=0)
    observed_kwh: float = Field(ge=0)
    tariff_id: UUID | None = None
    tariff_jod_per_kwh: float | None = Field(default=None, ge=0)
    tariff_version: str | None = None
    tariff_source: str | None = None
    tariff_name: str | None = None
    data_reliable: bool | None = None
    data_confidence: float | None = Field(default=None, ge=0, le=1)
    quality_score: float = Field(ge=0, le=100)
    calculation_timestamp: datetime
    window_start: datetime
    window_end: datetime
    forecast_reference: str | None = None
    observed_reference: str
    quality_reference: str


class RevenueRiskOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    tariff_id: UUID | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    method_version: str | None = None
    reason: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)


def revenue_risk_tool(data: RevenueRiskInput) -> RevenueRiskOutput:
    if data.data_reliable is None or data.data_confidence is None:
        reason = "Adapted reading quality is unavailable, so Revenue at Risk is unknown."
        return RevenueRiskOutput(status="unknown", reason=reason, warnings=[reason])
    if data.expected_kwh is None:
        reason = "Forecast is unavailable, so Revenue at Risk is unknown."
        return RevenueRiskOutput(status="unknown", reason=reason, warnings=[reason])
    result = estimate_revenue_at_risk(
        expected_kwh=data.expected_kwh,
        reliable_observed_kwh=data.observed_kwh,
        tariff_jod_per_kwh=data.tariff_jod_per_kwh,
        tariff_version=data.tariff_version,
        data_reliable=data.data_reliable,
        calculation_timestamp=data.calculation_timestamp,
        calculation_window_start=data.window_start,
        calculation_window_end=data.window_end,
        tariff_source=data.tariff_source,
        tariff_bracket_name=data.tariff_name,
        forecast_reference=data.forecast_reference,
        observed_reference=data.observed_reference,
        quality_reference=data.quality_reference,
        quality_score=data.quality_score,
    )
    if result["status"] == "error":
        raise ValueError(result["reason"])
    if result["status"] == "unknown":
        return RevenueRiskOutput(
            status="unknown", reason=result["reason"], warnings=[result["reason"]]
        )
    return RevenueRiskOutput(
        status="answered",
        tariff_id=data.tariff_id,
        values={key: value for key, value in result.items() if key != "status"},
        method_version=result["method_version"],
        confidence=data.data_confidence,
        evidence=[
            EvidenceReference(
                source=data.tariff_source or "tariff_unavailable",
                kind="revenue_at_risk",
                observed_at=data.calculation_timestamp,
                reliability=data.data_confidence,
                metadata={
                    "tariff_id": str(data.tariff_id),
                    "tariff_version": data.tariff_version,
                    "method_version": result["method_version"],
                },
            )
        ],
    )


class QueueCase(ToolOutput):
    case_id: str
    priority_score: float = Field(ge=0, le=100)
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class TriageInput(ToolInput):
    target_case_id: UUID
    technical_severity: float | None = Field(default=None, ge=0, le=100)
    scope_ratio: float | None = Field(default=None, ge=0, le=1)
    revenue_at_risk_jod: float | None = Field(default=None, ge=0)
    revenue_reference_jod: float = Field(default=10, gt=0)
    recurrence_score: float = Field(ge=0, le=1)
    upstream_evidence_score: float | None = Field(default=None, ge=0, le=1)
    data_confidence: float | None = Field(default=None, ge=0, le=1)
    waiting_sla_score: float = Field(default=0, ge=0, le=1)
    active_queue: list[QueueCase] = Field(default_factory=list)


class TriageOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    score: float | None = Field(default=None, ge=0, le=100)
    band: str | None = None
    active_rank: int | None = Field(default=None, ge=1)
    active_count: int | None = Field(default=None, ge=1)
    percentile: float | None = Field(default=None, ge=0, le=100)
    factors: dict[str, Any] = Field(default_factory=dict)
    policy_version: str | None = None
    revenue_available: bool


def triage_tool(data: TriageInput) -> TriageOutput:
    revenue_available = data.revenue_at_risk_jod is not None
    missing = [
        name for name in (
            "technical_severity", "upstream_evidence_score", "scope_ratio", "data_confidence"
        )
        if getattr(data, name) is None
    ]
    if missing:
        return TriageOutput(
            status="unknown",
            revenue_available=revenue_available,
            factors={"missing_inputs": missing},
            warnings=[f"Triage is unknown: unavailable {', '.join(missing)}."],
        )
    result = calculate_triage_priority(
        case_id=str(data.target_case_id),
        technical_severity=data.technical_severity,
        scope_ratio=data.scope_ratio,
        revenue_at_risk_jod=data.revenue_at_risk_jod or 0,
        revenue_reference_jod=data.revenue_reference_jod,
        recurrence_score=data.recurrence_score,
        upstream_evidence_score=data.upstream_evidence_score,
        data_confidence=data.data_confidence,
        waiting_sla_score=data.waiting_sla_score,
        active_queue=[item.model_dump() for item in data.active_queue],
    )
    if result["status"] != "success":
        raise ValueError(result["reason"])
    warnings = []
    if not revenue_available:
        warnings.append(
            "Revenue was unavailable; its triage contribution was explicitly set to zero."
        )
    return TriageOutput(
        status="answered",
        score=result["priority_score"],
        band=result["priority_band"],
        active_rank=result["rank"],
        active_count=result["queue_size"],
        percentile=result["percentile"],
        factors={
            "normalized": result["normalized_components"],
            "contributions": result["contributions"],
            "weights": result["weights"],
            "revenue": result["revenue"],
            "queue_semantics": result["queue_semantics"],
            "evidence_ownership": result["evidence_ownership"],
            "limitations": result["limitations"],
            "revenue_available": revenue_available,
        },
        policy_version=result["method_version"],
        revenue_available=revenue_available,
        warnings=warnings,
        evidence=[
            EvidenceReference(
                source="database.active_cases",
                kind="triage_priority",
                reliability=data.data_confidence,
                metadata={
                    "policy_version": result["method_version"],
                    "active_count": result["queue_size"],
                },
            )
        ],
    )
