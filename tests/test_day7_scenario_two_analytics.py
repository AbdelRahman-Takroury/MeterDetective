"""Scenario 2 proves current analytics respond to staged evidence."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest

import app.scenario_two_fixture as scenario_two_fixture
from app.scenario_two_fixture import (
    METER_IDS,
    RELATED_METERS,
    STAGE_A_TIME,
    STAGE_B_TIME,
    TARGET_METER,
    TARIFF,
    TOPOLOGY_ASSETS,
    TOPOLOGY_METER_CONNECTIONS,
    historical_readings,
    meter_series,
    stage_a_meter_readings,
    stage_a_transformer_reading,
    stage_b_meter_readings,
    stage_b_transformer_reading,
)
from app.services.investigation import InvestigationService
from app.tools.advanced import (
    EnergyBalanceInput,
    ForecastInput,
    HybridSeverityInput,
    IsolationInput,
    RevenueRiskInput,
    TriageInput,
    energy_balance_tool,
    forecast_tool,
    hybrid_severity_tool,
    isolation_tool,
    quality_for_day5,
    revenue_risk_tool,
    shared_scope_for_triage,
    triage_tool,
)
from app.tools.analytics import (
    AnomalyInput,
    BaselineInput,
    ConnectedAssetsInput,
    MeterSeries,
    PeerComparisonInput,
    PeerSelectionInput,
    QualityInput,
    ReadingPoint,
    SharedIncidentInput,
    calculate_baseline,
    compare_with_peers,
    detect_anomaly,
    detect_shared_incident,
    get_connected_assets,
    select_dynamic_peers,
    validate_reading_quality,
)

RUN_ID = UUID("72000000-0000-4000-8000-000000000001")
STAGE_A_CASE_ID = UUID("72000000-0000-4000-8000-000000000002")
STAGE_B_CASE_ID = UUID("72000000-0000-4000-8000-000000000003")


def _combine_readings(
    *collections: dict[str, tuple[ReadingPoint, ...]],
) -> dict[str, tuple[ReadingPoint, ...]]:
    return {
        meter_id: tuple(point for collection in collections for point in collection[meter_id])
        for meter_id in METER_IDS
    }


def _series_by_meter(
    readings_by_meter: dict[str, tuple[ReadingPoint, ...]],
) -> dict[str, MeterSeries]:
    return {series.meter_id: series for series in meter_series(readings_by_meter)}


def _event_value(series: MeterSeries, event_time) -> float:
    values = [point.kwh for point in series.readings if point.timestamp == event_time]
    assert len(values) == 1
    assert values[0] is not None
    return values[0]


def _analysis_at(
    event_time,
    readings_by_meter: dict[str, tuple[ReadingPoint, ...]],
    transformer_kwh: float,
) -> dict[str, object]:
    series_by_meter = _series_by_meter(readings_by_meter)
    target = series_by_meter[TARGET_METER]
    quality = validate_reading_quality(QualityInput(run_id=RUN_ID, readings=target.readings))
    baseline = calculate_baseline(
        BaselineInput(run_id=RUN_ID, readings=target.readings, cutoff=event_time)
    )
    anomaly = detect_anomaly(
        AnomalyInput(
            run_id=RUN_ID,
            readings=[point for point in target.readings if point.timestamp == event_time],
            baseline_profiles=baseline.profiles,
        )
    )
    selected_event = next(
        event
        for event in anomaly.events
        if event.timestamp == event_time and event.anomaly_type == "drop"
    )
    peer_selection = select_dynamic_peers(
        PeerSelectionInput(
            run_id=RUN_ID,
            target=target,
            candidates=[series_by_meter[meter_id] for meter_id in RELATED_METERS],
            event_time=event_time,
        )
    )
    selected_peers = [series_by_meter[peer.meter_id] for peer in peer_selection.peers]
    comparison = compare_with_peers(
        PeerComparisonInput(
            run_id=RUN_ID,
            target=target,
            peers=selected_peers,
            event_time=event_time,
        )
    )
    topology = get_connected_assets(
        ConnectedAssetsInput(
            run_id=RUN_ID,
            target_meter_id=TARGET_METER,
            assets=list(TOPOLOGY_ASSETS),
            meters=list(TOPOLOGY_METER_CONNECTIONS),
        )
    )
    connected_series = [series_by_meter[meter_id] for meter_id in topology.connected_meter_ids]
    shared = detect_shared_incident(
        SharedIncidentInput(
            run_id=RUN_ID,
            connected_series=connected_series,
            event_time=event_time,
        )
    )
    energy = energy_balance_tool(
        EnergyBalanceInput(
            run_id=RUN_ID,
            transformer_id=stage_a_transformer_reading().transformer_id,
            event_time=event_time,
            transformer_kwh=transformer_kwh,
            downstream_kwh=[
                _event_value(series_by_meter[meter_id], event_time) for meter_id in METER_IDS
            ],
        )
    )
    hypotheses = InvestigationService._hypotheses(quality, shared)
    return {
        "quality": quality,
        "baseline": baseline,
        "anomaly": anomaly,
        "selected_event": selected_event,
        "peer_selection": peer_selection,
        "comparison": comparison,
        "topology": topology,
        "shared": shared,
        "energy": energy,
        "hypotheses": hypotheses,
    }


def _hypotheses_by_label(analysis: dict[str, object]) -> dict[str, float]:
    hypotheses = analysis["hypotheses"]
    return {item.label: item.confidence for item in hypotheses}  # type: ignore[union-attr]


def _profile_for(analysis: dict[str, object], event_time):
    baseline = analysis["baseline"]
    return next(
        profile
        for profile in baseline.profiles  # type: ignore[union-attr]
        if (profile.weekday, profile.hour, profile.minute)
        == (event_time.weekday(), event_time.hour, event_time.minute)
    )


def _day5_analysis_at(
    event_time,
    readings_by_meter: dict[str, tuple[ReadingPoint, ...]],
    transformer_kwh: float,
    case_id: UUID,
) -> dict[str, object]:
    analysis = _analysis_at(event_time, readings_by_meter, transformer_kwh)
    target = _series_by_meter(readings_by_meter)[TARGET_METER]
    quality = analysis["quality"]
    selected_event = analysis["selected_event"]
    comparison = analysis["comparison"]
    shared = analysis["shared"]
    energy = analysis["energy"]

    forecast = forecast_tool(
        ForecastInput(
            run_id=RUN_ID,
            meter_id=TARGET_METER,
            target_timestamp=event_time,
            readings=target.readings,
        )
    )
    isolation = isolation_tool(
        IsolationInput(
            run_id=RUN_ID,
            meter_id=TARGET_METER,
            target_timestamp=event_time,
            readings=target.readings,
        )
    )
    hybrid = hybrid_severity_tool(
        HybridSeverityInput(
            run_id=RUN_ID,
            anomaly=selected_event,  # type: ignore[arg-type]
            comparison=comparison,  # type: ignore[arg-type]
            isolation=isolation,
        )
    )
    adapted_quality = quality_for_day5(quality)  # type: ignore[arg-type]
    adapted_scope = shared_scope_for_triage(shared)  # type: ignore[arg-type]
    revenue = revenue_risk_tool(
        RevenueRiskInput(
            run_id=RUN_ID,
            expected_kwh=forecast.expected_kwh,
            observed_kwh=selected_event.observed_kwh or 0,  # type: ignore[union-attr]
            tariff_jod_per_kwh=float(TARIFF.jod_per_kwh),
            tariff_version=TARIFF.name,
            tariff_source=TARIFF.source,
            tariff_name=TARIFF.name,
            data_reliable=(
                adapted_quality.values["data_reliable"] and selected_event.observed_kwh is not None  # type: ignore[union-attr]
                if adapted_quality.status == "answered"
                else None
            ),
            data_confidence=adapted_quality.values.get("data_confidence"),
            quality_score=quality.quality_score,  # type: ignore[union-attr]
            calculation_timestamp=event_time,
            window_start=event_time,
            window_end=event_time + timedelta(minutes=30),
            forecast_reference=forecast.method_version,
            observed_reference=f"fixture:{TARGET_METER}:{event_time.isoformat()}",
            quality_reference=f"fixture:{RUN_ID}:quality",
        )
    )
    triage = triage_tool(
        TriageInput(
            run_id=RUN_ID,
            target_case_id=case_id,
            technical_severity=hybrid.values.get("severity_score"),
            scope_ratio=adapted_scope.values.get("scope_ratio"),
            revenue_at_risk_jod=(
                revenue.values.get("revenue_at_risk_jod", {}).get("base")
                if revenue.status == "answered"
                else None
            ),
            recurrence_score=0,
            upstream_evidence_score=energy.triage_adapter.get("upstream_evidence_score"),  # type: ignore[union-attr]
            data_confidence=adapted_quality.values.get("data_confidence"),
            active_queue=[],
        )
    )
    return {
        **analysis,
        "forecast": forecast,
        "isolation": isolation,
        "hybrid": hybrid,
        "adapted_quality": adapted_quality,
        "adapted_scope": adapted_scope,
        "revenue": revenue,
        "triage": triage,
    }


# Stage A — initial local evidence
def test_stage_a_supports_local_hypothesis() -> None:
    analysis = _analysis_at(
        STAGE_A_TIME,
        _combine_readings(historical_readings(), stage_a_meter_readings()),
        stage_a_transformer_reading().input_kwh,
    )

    quality = analysis["quality"]
    profile = _profile_for(analysis, STAGE_A_TIME)
    event = analysis["selected_event"]
    selection = analysis["peer_selection"]
    comparison = analysis["comparison"]
    topology = analysis["topology"]
    shared = analysis["shared"]
    energy = analysis["energy"]
    hypotheses = _hypotheses_by_label(analysis)

    assert quality.reliable is True  # type: ignore[union-attr]
    assert profile.median_kwh == pytest.approx(9.7)
    assert event.severity == pytest.approx(69.07)  # type: ignore[union-attr]
    assert [peer.meter_id for peer in selection.peers] == list(RELATED_METERS)  # type: ignore[union-attr]
    assert topology.connected_meter_ids == list(RELATED_METERS)  # type: ignore[union-attr]
    assert comparison.peer_median_kwh == pytest.approx(10.6)  # type: ignore[union-attr]
    assert comparison.deviation_pct == pytest.approx(-71.7)  # type: ignore[union-attr]
    assert shared.affected_count == 0  # type: ignore[union-attr]
    assert shared.answered_count == 2  # type: ignore[union-attr]
    assert shared.affected_fraction == 0.0  # type: ignore[union-attr]
    assert shared.incident_type == "local"  # type: ignore[union-attr]
    assert energy.balance_status == "balanced"  # type: ignore[union-attr]
    assert hypotheses["individual_meter_malfunction"] == pytest.approx(0.7)
    assert hypotheses["shared_upstream_or_transformer_issue"] == pytest.approx(0.15)


# Stage B — later shared evidence
def test_stage_b_supports_shared_replan() -> None:
    analysis = _analysis_at(
        STAGE_B_TIME,
        _combine_readings(
            historical_readings(), stage_a_meter_readings(), stage_b_meter_readings()
        ),
        stage_b_transformer_reading().input_kwh,
    )

    profile = _profile_for(analysis, STAGE_B_TIME)
    event = analysis["selected_event"]
    selection = analysis["peer_selection"]
    comparison = analysis["comparison"]
    shared = analysis["shared"]
    energy = analysis["energy"]
    hypotheses = _hypotheses_by_label(analysis)

    assert profile.median_kwh == pytest.approx(9.75)
    assert event.severity == pytest.approx(69.23)  # type: ignore[union-attr]
    assert [peer.meter_id for peer in selection.peers] == list(RELATED_METERS)  # type: ignore[union-attr]
    assert comparison.peer_median_kwh == pytest.approx(3.0)  # type: ignore[union-attr]
    assert comparison.deviation_pct == 0.0  # type: ignore[union-attr]
    assert shared.affected_count == 2  # type: ignore[union-attr]
    assert shared.answered_count == 2  # type: ignore[union-attr]
    assert shared.affected_fraction == 1.0  # type: ignore[union-attr]
    assert shared.incident_type == "shared"  # type: ignore[union-attr]
    assert shared.confidence == 1.0  # type: ignore[union-attr]
    assert energy.balance_status == "imbalanced"  # type: ignore[union-attr]
    assert energy.values["expected_transformer_kwh"] == pytest.approx(9.27)  # type: ignore[union-attr]
    assert energy.values["actual_transformer_kwh"] == pytest.approx(24.926)  # type: ignore[union-attr]
    assert energy.triage_adapter["upstream_evidence_score"] == 1.0  # type: ignore[union-attr]
    assert hypotheses["individual_meter_malfunction"] == pytest.approx(0.2)
    assert hypotheses["shared_upstream_or_transformer_issue"] == pytest.approx(1.0)


# Counter-example — normal related meters
def test_normal_peers_do_not_trigger_shared_replan() -> None:
    counterexample_stage_b = {
        meter_id: (ReadingPoint(timestamp=STAGE_B_TIME, kwh=kwh, quality_flag="valid"),)
        for meter_id, kwh in zip(METER_IDS, (3.0, 10.4, 10.8), strict=True)
    }
    analysis = _analysis_at(
        STAGE_B_TIME,
        _combine_readings(historical_readings(), stage_a_meter_readings(), counterexample_stage_b),
        stage_a_transformer_reading().input_kwh,
    )

    event = analysis["selected_event"]
    shared = analysis["shared"]
    energy = analysis["energy"]
    hypotheses = _hypotheses_by_label(analysis)

    assert event.anomaly_type == "drop"  # type: ignore[union-attr]
    assert shared.affected_count == 0  # type: ignore[union-attr]
    assert shared.affected_fraction == 0.0  # type: ignore[union-attr]
    assert shared.incident_type == "local"  # type: ignore[union-attr]
    assert energy.balance_status == "balanced"  # type: ignore[union-attr]
    assert energy.triage_adapter["upstream_evidence_score"] == 0.0  # type: ignore[union-attr]
    assert (
        hypotheses["individual_meter_malfunction"]
        > hypotheses["shared_upstream_or_transformer_issue"]
    )


# Temporal leakage and determinism
def test_stage_a_is_unchanged_by_later_stage_b_readings() -> None:
    stage_a_evidence = _combine_readings(historical_readings(), stage_a_meter_readings())
    evidence_with_later_readings = _combine_readings(
        historical_readings(), stage_a_meter_readings(), stage_b_meter_readings()
    )

    stage_a = _analysis_at(
        STAGE_A_TIME,
        stage_a_evidence,
        stage_a_transformer_reading().input_kwh,
    )
    stage_a_with_later_readings = _analysis_at(
        STAGE_A_TIME,
        evidence_with_later_readings,
        stage_a_transformer_reading().input_kwh,
    )

    assert (
        _profile_for(stage_a, STAGE_A_TIME).model_dump()
        == _profile_for(stage_a_with_later_readings, STAGE_A_TIME).model_dump()
    )
    assert (
        stage_a["selected_event"].model_dump()
        == stage_a_with_later_readings["selected_event"].model_dump()
    )  # type: ignore[union-attr]
    assert (
        stage_a["peer_selection"].model_dump()
        == stage_a_with_later_readings["peer_selection"].model_dump()
    )  # type: ignore[union-attr]
    assert (
        stage_a["comparison"].model_dump() == stage_a_with_later_readings["comparison"].model_dump()
    )  # type: ignore[union-attr]


def test_scenario_two_analytics_are_deterministic() -> None:
    evidence = _combine_readings(
        historical_readings(), stage_a_meter_readings(), stage_b_meter_readings()
    )
    first = _day5_analysis_at(
        STAGE_B_TIME,
        evidence,
        stage_b_transformer_reading().input_kwh,
        STAGE_B_CASE_ID,
    )
    second = _day5_analysis_at(
        STAGE_B_TIME,
        evidence,
        stage_b_transformer_reading().input_kwh,
        STAGE_B_CASE_ID,
    )

    for key in ("isolation", "hybrid", "revenue", "triage"):
        assert first[key].model_dump(mode="json") == second[key].model_dump(mode="json")  # type: ignore[union-attr]


# Revenue and triage contract
def test_scenario_two_revenue_contract() -> None:
    stage_a = _day5_analysis_at(
        STAGE_A_TIME,
        _combine_readings(historical_readings(), stage_a_meter_readings()),
        stage_a_transformer_reading().input_kwh,
        STAGE_A_CASE_ID,
    )
    stage_b = _day5_analysis_at(
        STAGE_B_TIME,
        _combine_readings(
            historical_readings(), stage_a_meter_readings(), stage_b_meter_readings()
        ),
        stage_b_transformer_reading().input_kwh,
        STAGE_B_CASE_ID,
    )

    revenue_a = stage_a["revenue"]
    revenue_b = stage_b["revenue"]
    forecast_a = stage_a["forecast"]
    forecast_b = stage_b["forecast"]

    assert revenue_a.status == "answered"  # type: ignore[union-attr]
    assert revenue_b.status == "answered"  # type: ignore[union-attr]
    assert forecast_a.expected_kwh == pytest.approx(9.7)  # type: ignore[union-attr]
    assert forecast_b.expected_kwh == pytest.approx(9.75)  # type: ignore[union-attr]
    assert revenue_a.values["expected_missing_kwh"] == pytest.approx(6.7)  # type: ignore[union-attr]
    assert revenue_b.values["expected_missing_kwh"] == pytest.approx(6.75)  # type: ignore[union-attr]
    assert revenue_a.values["revenue_at_risk_jod"] == {
        "low": pytest.approx(0.4824),
        "base": pytest.approx(0.6432),
        "high": pytest.approx(0.804),
    }  # type: ignore[union-attr]
    assert revenue_b.values["revenue_at_risk_jod"] == {
        "low": pytest.approx(0.486),
        "base": pytest.approx(0.648),
        "high": pytest.approx(0.81),
    }  # type: ignore[union-attr]
    assert revenue_a.values["tariff"]["jod_per_kwh"] == pytest.approx(0.12)  # type: ignore[union-attr]
    assert revenue_a.values["tariff"]["version"] == TARIFF.name  # type: ignore[union-attr]
    assert revenue_a.values["tariff"]["source"] == TARIFF.source  # type: ignore[union-attr]
    assert (
        revenue_b.values["revenue_at_risk_jod"]["base"]
        > revenue_a.values[  # type: ignore[union-attr]
            "revenue_at_risk_jod"
        ]["base"]
    )


def test_scenario_two_triage_contract() -> None:
    stage_a = _day5_analysis_at(
        STAGE_A_TIME,
        _combine_readings(historical_readings(), stage_a_meter_readings()),
        stage_a_transformer_reading().input_kwh,
        STAGE_A_CASE_ID,
    )
    stage_b = _day5_analysis_at(
        STAGE_B_TIME,
        _combine_readings(
            historical_readings(), stage_a_meter_readings(), stage_b_meter_readings()
        ),
        stage_b_transformer_reading().input_kwh,
        STAGE_B_CASE_ID,
    )

    isolation_a = stage_a["isolation"]
    isolation_b = stage_b["isolation"]
    hybrid_a = stage_a["hybrid"]
    hybrid_b = stage_b["hybrid"]
    scope_a = stage_a["adapted_scope"]
    scope_b = stage_b["adapted_scope"]
    energy_a = stage_a["energy"]
    energy_b = stage_b["energy"]
    quality_a = stage_a["adapted_quality"]
    quality_b = stage_b["adapted_quality"]
    triage_a = stage_a["triage"]
    triage_b = stage_b["triage"]

    assert isolation_a.status == "answered"  # type: ignore[union-attr]
    assert isolation_b.status == "answered"  # type: ignore[union-attr]
    assert hybrid_a.status == "answered"  # type: ignore[union-attr]
    assert hybrid_b.status == "answered"  # type: ignore[union-attr]
    assert scope_a.values["scope_ratio"] == 0.0  # type: ignore[union-attr]
    assert scope_b.values["scope_ratio"] == 1.0  # type: ignore[union-attr]
    assert energy_a.triage_adapter["upstream_evidence_score"] == 0.0  # type: ignore[union-attr]
    assert energy_b.triage_adapter["upstream_evidence_score"] == 1.0  # type: ignore[union-attr]
    assert quality_a.values["data_confidence"] == 1.0  # type: ignore[union-attr]
    assert quality_b.values["data_confidence"] == 1.0  # type: ignore[union-attr]
    assert triage_a.status == "answered"  # type: ignore[union-attr]
    assert triage_b.status == "answered"  # type: ignore[union-attr]
    assert triage_a.active_rank == triage_b.active_rank == 1  # type: ignore[union-attr]
    assert triage_a.active_count == triage_b.active_count == 1  # type: ignore[union-attr]
    assert triage_a.band == "P4"  # type: ignore[union-attr]
    assert triage_b.band == "P3"  # type: ignore[union-attr]
    assert triage_b.score > triage_a.score  # type: ignore[operator, union-attr]
    assert hybrid_b.values["severity_score"] < hybrid_a.values["severity_score"]  # type: ignore[union-attr]


def test_scenario_two_fixture_contains_no_expected_labels() -> None:
    forbidden_names = {
        "expected_result",
        "expected_classification",
        "expected_hypothesis",
        "expected_confidence",
        "should_replan",
        "is_shared_incident",
        "ground_truth_cause",
        "fraud_label",
        "root_cause_label",
        "expected_triage",
    }
    public_names = {name for name in vars(scenario_two_fixture) if not name.startswith("_")}
    fixture_field_names = {
        field_name
        for fixture_type in (
            scenario_two_fixture.MeterMetadata,
            scenario_two_fixture.TransformerReadingFixture,
            scenario_two_fixture.TariffFixture,
        )
        for field_name in fixture_type.__dataclass_fields__
    }

    assert forbidden_names.isdisjoint(public_names)
    assert forbidden_names.isdisjoint(fixture_field_names)
