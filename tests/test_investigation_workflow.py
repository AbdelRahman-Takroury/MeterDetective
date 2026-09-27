from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base
from app.db.repositories import HypothesisRepository
from app.db.session import get_db
from app.main import app
from app.services import investigation as investigation_module
from app.services.investigation import InvestigationService, ReplayCommand, build_registry
from app.tools import advanced
from app.tools.advanced_analytics import (
    adapt_day3_anomaly_event_for_severity,
    adapt_day4_peer_comparison_for_severity,
    adapt_energy_balance_for_triage,
    calculate_anomaly_severity,
)
from app.tools.database import PrecedentInput, find_meter_precedents
from app.tools.weather import WeatherClient


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as value:
        yield value


def seed_scenario(session: Session, *, shared: bool = False) -> datetime:
    substation = models.Asset(
        id=uuid4(), asset_type="substation", name="S1", metadata_json={}
    )
    feeder = models.Asset(
        id=uuid4(),
        parent_id=substation.id,
        asset_type="feeder",
        name="F1",
        metadata_json={},
    )
    transformer = models.Asset(
        id=uuid4(),
        parent_id=feeder.id,
        asset_type="transformer",
        name="T1",
        metadata_json={},
    )
    session.add_all([substation, feeder, transformer])
    session.add_all(
        [
            models.Meter(
                id=meter_id,
                transformer_id=transformer.id,
                type="smart",
                status="active",
                customer_segment="residential",
                has_solar=False,
                has_ev=False,
                metadata_source="synthetic-test",
                metadata_json={},
            )
            for meter_id in ("M1", "M2", "M3")
        ]
    )
    event_time = datetime(2026, 9, 20, 12, tzinfo=UTC)
    start = event_time - timedelta(days=28)
    intervals = int((event_time - start).total_seconds() / 1800)
    for index in range(intervals):
        timestamp = start + timedelta(minutes=30 * index)
        shape = ((index % 12) - 6) * 0.05
        for meter_id, offset in (("M1", 0.0), ("M2", 0.4), ("M3", 0.8)):
            session.add(
                models.Reading(
                    meter_id=meter_id,
                    timestamp=timestamp,
                    kwh=10 + offset + shape,
                    quality_flag="valid",
                    source="synthetic-test",
                )
            )
    session.add(
        models.Reading(
            meter_id="M1",
            timestamp=event_time,
            kwh=3,
            quality_flag="valid",
            source="synthetic-test",
        )
    )
    for meter_id, normal in (("M2", 10.4), ("M3", 10.8)):
        session.add(
            models.Reading(
                meter_id=meter_id,
                timestamp=event_time,
                kwh=3 if shared else normal,
                quality_flag="valid",
                source="synthetic-test",
            )
        )
    session.add(models.TransformerReading(
        transformer_id=transformer.id,
        timestamp=event_time,
        input_kwh=(9 if shared else 24.2) * 1.03,
    ))
    session.commit()
    return event_time


def test_replay_creates_one_inspectable_case_and_is_idempotent(session: Session) -> None:
    event_time = seed_scenario(session)

    def unavailable_weather(url: str, timeout: float):
        raise TimeoutError

    service = InvestigationService(
        session,
        build_registry(
            weather_client=WeatherClient(
                base_url="https://weather.invalid",
                retry_count=1,
                transport=unavailable_weather,
            )
        ),
    )
    first = service.replay(ReplayCommand(meter_id="M1", event_time=event_time))
    session.commit()

    assert first.status == "case_created"
    assert first.case_id is not None
    assert first.report_id is not None
    assert first.tool_calls == 20
    assert session.scalar(select(func.count()).select_from(models.AgentRun)) == 1
    assert session.scalar(select(func.count()).select_from(models.ToolExecution)) == 20
    assert session.scalar(select(func.count()).select_from(models.Case)) == 1

    report = session.get(models.InvestigationReport, first.report_id)
    assert report is not None
    assert len(report.answers_json) == 18
    assert {item["question_id"] for item in report.answers_json} == set(range(1, 19))
    assert report.answers_json[14]["status"] == "pending_verification"
    assert all(item["status"] for item in report.answers_json)
    answers = {item["question_id"]: item for item in report.answers_json}
    assert answers[6]["status"] == "unknown"
    assert answers[12]["limitations"] == [
        "No relevant stored technical document was retrieved."
    ]
    assert answers[16]["status"] == "unknown"
    assert "tariff" in answers[16]["limitations"][0].lower()
    assert answers[18]["status"] == "answered"
    assert any("Revenue was unavailable" in item for item in answers[18]["limitations"])
    assert session.scalar(select(func.count()).select_from(models.FinancialImpact)) == 0

    hypotheses = list(
        session.scalars(
            select(models.Hypothesis)
            .where(models.Hypothesis.case_id == first.case_id)
            .order_by(models.Hypothesis.confidence.desc())
        )
    )
    assert hypotheses[0].label == "individual_meter_malfunction"
    assert hypotheses[0].support_json
    assert hypotheses[0].update_history_json
    assert any(item.contradiction_json for item in hypotheses)

    second = InvestigationService(session).replay(
        ReplayCommand(meter_id="M1", event_time=event_time)
    )
    assert second.duplicate is True
    assert second.case_id == first.case_id
    assert session.scalar(select(func.count()).select_from(models.Case)) == 1


def test_active_queue_change_versions_triage_and_financial_history(session: Session) -> None:
    event_time = seed_scenario(session)
    session.add(
        models.Tariff(
            name="Synthetic residential test tariff",
            customer_segment="residential",
            currency="JOD",
            jod_per_kwh=Decimal("0.120000"),
            effective_from=event_time - timedelta(days=1),
            source="fixture:day5-j",
            is_synthetic=True,
        )
    )
    session.commit()

    first = InvestigationService(session).replay(
        ReplayCommand(meter_id="M1", event_time=event_time)
    )
    session.commit()
    first_report = session.get(models.InvestigationReport, first.report_id)
    assert first_report is not None
    first_q16 = next(item for item in first_report.answers_json if item["question_id"] == 16)
    assert first_q16["status"] == "answered"
    assert session.scalar(
        select(func.count()).select_from(models.FinancialImpact).where(
            models.FinancialImpact.case_id == first.case_id
        )
    ) == 1

    second_time = event_time + timedelta(minutes=30)
    session.add_all(
        [
            models.Reading(
                meter_id="M1",
                timestamp=second_time,
                kwh=10,
                quality_flag="valid",
                source="synthetic-test",
            ),
            models.Reading(
                meter_id="M2",
                timestamp=second_time,
                kwh=2,
                quality_flag="valid",
                source="synthetic-test",
            ),
            models.Reading(
                meter_id="M3",
                timestamp=second_time,
                kwh=10.8,
                quality_flag="valid",
                source="synthetic-test",
            ),
        ]
    )
    session.commit()
    session.add(models.TransformerReading(
        transformer_id=session.get(models.Meter, "M2").transformer_id,
        timestamp=second_time, input_kwh=22.8 * 1.03,
    ))
    session.flush()
    second = InvestigationService(session).replay(
        ReplayCommand(meter_id="M2", event_time=second_time)
    )
    session.commit()

    assert second.status == "case_created"
    versions = list(
        session.scalars(
            select(models.InvestigationReport)
            .where(models.InvestigationReport.case_id == first.case_id)
            .order_by(models.InvestigationReport.version)
        )
    )
    assert [item.version for item in versions] == [1, 2]
    old_triage = session.scalar(
        select(models.TriageAssessment).where(
            models.TriageAssessment.report_id == versions[0].id
        )
    )
    new_triage = session.scalar(
        select(models.TriageAssessment).where(
            models.TriageAssessment.report_id == versions[1].id
        )
    )
    assert old_triage is not None and new_triage is not None
    assert old_triage.active_count == 1
    assert new_triage.active_count == 2
    assert new_triage.policy_version == old_triage.policy_version
    updated_q18 = next(item for item in versions[1].answers_json if item["question_id"] == 18)
    assert updated_q18["structured_values"]["active_count"] == 2
    assert updated_q18["fresh_as_of"] != versions[0].answers_json[17]["fresh_as_of"]
    assert session.scalar(
        select(func.count()).select_from(models.FinancialImpact).where(
            models.FinancialImpact.case_id == first.case_id
        )
    ) == 2
    assert session.scalar(
        select(func.count()).select_from(models.CaseEvent).where(
            models.CaseEvent.case_id == first.case_id,
            models.CaseEvent.event_type == "triage_recalculated",
        )
    ) == 1


def test_shared_evidence_changes_leading_hypothesis(session: Session) -> None:
    event_time = seed_scenario(session, shared=True)
    result = InvestigationService(session).replay(
        ReplayCommand(meter_id="M1", event_time=event_time)
    )
    session.commit()

    hypotheses = list(
        session.scalars(
            select(models.Hypothesis)
            .where(models.Hypothesis.case_id == result.case_id)
            .order_by(models.Hypothesis.confidence.desc())
        )
    )
    assert hypotheses[0].label == "shared_upstream_or_transformer_issue"
    assert hypotheses[0].support_json
    assert hypotheses[1].contradiction_json


def test_unknown_tool_failure_is_persisted(session: Session) -> None:
    seed_scenario(session)
    run = models.AgentRun(trigger={}, status="running", state_json={})
    session.add(run)
    session.flush()
    from app.tools.database import MeterProfileInput
    from app.tools.registry import ToolRegistry

    execution = ToolRegistry().execute(
        "missing_tool", MeterProfileInput(run_id=run.id, meter_id="M1"), session
    )

    assert execution.status.value == "failed"
    trace = session.scalar(
        select(models.ToolExecution).where(models.ToolExecution.run_id == run.id)
    )
    assert trace is not None
    assert trace.error["code"] == "unknown_tool"


def test_hypothesis_confidence_can_increase_and_decrease_with_audit_history(
    session: Session,
) -> None:
    event_time = seed_scenario(session)
    result = InvestigationService(session).replay(
        ReplayCommand(meter_id="M1", event_time=event_time)
    )
    hypothesis = session.scalar(
        select(models.Hypothesis)
        .where(
            models.Hypothesis.case_id == result.case_id,
            models.Hypothesis.label == "individual_meter_malfunction",
        )
    )
    evidence = session.scalar(
        select(models.Evidence).where(
            models.Evidence.case_id == result.case_id,
            models.Evidence.kind == "anomaly",
        )
    )
    assert hypothesis is not None and evidence is not None
    repository = HypothesisRepository(session)
    increased = repository.update_confidence(
        hypothesis.id,
        confidence=0.85,
        reason="Additional local anomaly evidence",
        supporting_evidence_ids=[evidence.id],
    )
    assert increased.confidence == 0.85
    decreased = repository.update_confidence(
        hypothesis.id,
        confidence=0.35,
        reason="New shared evidence contradicts the local explanation",
        contradicting_evidence_ids=[evidence.id],
    )
    assert decreased.confidence == 0.35
    assert len(decreased.update_history_json) == 3
    events = list(
        session.scalars(
            select(models.CaseEvent).where(
                models.CaseEvent.case_id == result.case_id,
                models.CaseEvent.event_type == "hypothesis_updated",
            )
        )
    )
    assert len(events) == 2


def test_precedents_keep_exact_meter_results_separate_and_first(session: Session) -> None:
    seed_scenario(session)
    exact = models.Case(title="Exact prior case", status="closed")
    similar = models.Case(title="Similar segment case", status="resolved")
    session.add_all([exact, similar])
    session.flush()
    session.add_all(
        [
            models.CaseMeter(case_id=exact.id, meter_id="M1", relationship="affected"),
            models.CaseMeter(case_id=similar.id, meter_id="M2", relationship="affected"),
        ]
    )
    session.flush()

    result = find_meter_precedents(
        PrecedentInput(run_id=uuid4(), meter_id="M1"), session
    )

    assert [item.case_id for item in result.exact_meter_cases] == [exact.id]
    assert [item.case_id for item in result.similar_system_cases] == [similar.id]
    assert result.exact_meter_cases[0].match_type == "exact_meter"


def test_replay_case_detail_and_trace_are_exposed_through_api() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        event_time = seed_scenario(setup)

    def override_db() -> Iterator[Session]:
        with factory() as database:
            yield database

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            replay = client.post(
                "/api/replay/step",
                json={"meter_id": "M1", "event_time": event_time.isoformat()},
            )
            assert replay.status_code == 200, replay.text
            replay_body = replay.json()
            assert replay_body["tool_calls"] == 20
            case_id = replay_body["case_id"]

            detail = client.get(f"/api/cases/{case_id}")
            assert detail.status_code == 200
            detail_body = detail.json()
            assert len(detail_body["latest_report"]["answers"]) == 18
            assert len(detail_body["hypotheses"]) >= 3

            trace = client.get(f"/api/cases/{case_id}/trace")
            assert trace.status_code == 200
            tools = trace.json()["runs"][0]["tools"]
            assert len(tools) == 20
            assert all(tool["status"] == "succeeded" for tool in tools)
    finally:
        app.dependency_overrides.clear()



def test_replay_uses_hybrid_and_energy_adapters_in_persisted_triage(session: Session) -> None:
    event_time = seed_scenario(session)
    transformer = session.scalar(select(models.TransformerReading))
    transformer.input_kwh = 30  # Imbalanced, but adapter strength is less than confidence=1.
    session.flush()
    result = InvestigationService(session).replay(
        ReplayCommand(meter_id="M1", event_time=event_time)
    )
    traces = {
        row.tool_name: row for row in session.scalars(
            select(models.ToolExecution).where(models.ToolExecution.run_id == result.run_id)
        )
    }
    hybrid_trace = traces["calculate_anomaly_severity"]
    raw = hybrid_trace.input_json["anomaly"]
    peer = traces["compare_with_peers"].output_json
    isolation = traces["detect_isolation_forest_anomaly"].output_json
    day3 = adapt_day3_anomaly_event_for_severity(
        raw["anomaly_type"], raw["components"]["deviation_pct"], raw["severity"]
    )
    day4 = adapt_day4_peer_comparison_for_severity(peer["deviation_pct"])
    expected = calculate_anomaly_severity(
        day3["usage_deviation_fraction"], day3["rule_based_score"],
        isolation["values"]["isolation_score"], day4["peer_deviation_score"],
    )
    hybrid = hybrid_trace.output_json["values"]
    assert isolation["status"] == "answered"
    assert hybrid_trace.input_json["isolation"] == isolation
    assert hybrid["day3_adapter"] == day3
    assert hybrid["day4_adapter"] == day4
    assert hybrid["severity_score"] == expected["severity_score"]
    assert hybrid["contributions"] == expected["contributions"]
    assert hybrid["contributions"]["rule_based"] == 0  # Magnitude is owned by usage.
    assert hybrid["severity_score"] != raw["severity"]
    energy = traces["calculate_energy_balance"].output_json
    adapter = adapt_energy_balance_for_triage({"status": "success", **energy["values"]})
    triage_trace = traces["calculate_triage_priority"]
    assert triage_trace.input_json["technical_severity"] == hybrid["severity_score"]
    assert triage_trace.input_json["upstream_evidence_score"] == adapter["upstream_evidence_score"]
    assert adapter["upstream_evidence_score"] != energy["confidence"]
    assessment = session.scalar(
        select(models.TriageAssessment).where(models.TriageAssessment.case_id == result.case_id)
    )
    assert assessment.score == triage_trace.output_json["score"]
    anomaly = session.scalar(select(models.Anomaly))
    assert anomaly.severity == raw["severity"]
    assert anomaly.features_json["severity"] == raw["severity"]
    assert hybrid["raw_day3_severity"] == raw["severity"]
    evidence = list(session.scalars(
        select(models.Evidence).where(models.Evidence.case_id == result.case_id)
    ))
    assert {"isolation", "hybrid_severity"} <= {item.kind for item in evidence}
    report = session.get(models.InvestigationReport, result.report_id)
    q2 = next(item for item in report.answers_json if item["question_id"] == 2)
    assert q2["structured_values"]["severity"] == hybrid["severity_score"]
    assert q2["structured_values"]["raw_day3_severity"] == raw["severity"]


@pytest.mark.parametrize("missing", ["isolation", "peers", "upstream"])
def test_replay_preserves_unknown_day5_evidence(session: Session, missing: str) -> None:
    event_time = seed_scenario(session)
    if missing == "isolation":
        # Enough seasonal history for Day 3; insufficient contiguous target context for IF.
        session.execute(delete(models.Reading).where(
            models.Reading.meter_id == "M1",
            models.Reading.timestamp == event_time - timedelta(minutes=30),
        ))
    elif missing == "peers":
        session.execute(delete(models.Reading).where(
            models.Reading.meter_id.in_(["M2", "M3"]),
            models.Reading.timestamp == event_time,
        ))
    else:
        session.execute(delete(models.TransformerReading))
    session.flush()
    result = InvestigationService(session).replay(
        ReplayCommand(meter_id="M1", event_time=event_time)
    )
    assert result.status == "case_created"
    traces = {
        row.tool_name: row for row in session.scalars(
            select(models.ToolExecution).where(models.ToolExecution.run_id == result.run_id)
        )
    }
    triage = traces["calculate_triage_priority"]
    assert triage.output_json["status"] == "unknown"
    assert triage.output_json["score"] is None
    assert triage.output_json["active_rank"] is None
    hybrid = traces["calculate_anomaly_severity"].output_json
    if missing in {"isolation", "peers"}:
        assert hybrid["status"] == "unknown"
        assert "severity_score" not in hybrid["values"]
        assert triage.input_json["technical_severity"] is None
    if missing == "isolation":
        assert traces["detect_isolation_forest_anomaly"].output_json["status"] == "unknown"
    if missing == "upstream":
        assert hybrid["status"] == "answered"
        assert triage.input_json["upstream_evidence_score"] is None
    assert session.scalar(
        select(func.count()).select_from(models.TriageAssessment)
    ) == 0
    assert session.get(models.Case, result.case_id).triage_score is None
    report = session.get(models.InvestigationReport, result.report_id)
    q18 = next(item for item in report.answers_json if item["question_id"] == 18)
    assert q18["status"] == "unknown"
    assert q18["structured_values"]["score"] is None



@pytest.mark.parametrize("scope_available", [True, False])
def test_replay_adapts_zero_and_unknown_scope(
    session: Session, monkeypatch: pytest.MonkeyPatch, scope_available: bool
) -> None:
    event_time = seed_scenario(session)
    session.add(models.Tariff(
        name="Scope fixture tariff", customer_segment="residential", currency="JOD",
        jod_per_kwh=Decimal("0.12"), effective_from=event_time - timedelta(days=1),
        source="fixture:scope", is_synthetic=True,
    ))
    if not scope_available:
        # Leave current readings and peer evidence intact, but remove M3's shared history.
        session.execute(delete(models.Reading).where(
            models.Reading.meter_id == "M3",
            models.Reading.timestamp < event_time,
        ))
    session.flush()
    spy = Mock(wraps=advanced.adapt_day4_shared_incident_for_triage)
    monkeypatch.setattr(advanced, "adapt_day4_shared_incident_for_triage", spy)
    result = InvestigationService(session).replay(
        ReplayCommand(meter_id="M1", event_time=event_time)
    )
    traces = {row.tool_name: row for row in session.scalars(
        select(models.ToolExecution).where(models.ToolExecution.run_id == result.run_id)
    )}
    shared = traces["detect_shared_incident"].output_json
    spy.assert_called_once_with(
        shared_status=shared["status"], affected_fraction=shared["affected_fraction"],
        incident_type=shared["incident_type"], shared_confidence=shared["confidence"],
    )
    triage = traces["calculate_triage_priority"]
    for name in ("technical_severity", "upstream_evidence_score", "data_confidence",
                 "revenue_at_risk_jod", "recurrence_score", "waiting_sla_score"):
        assert triage.input_json[name] is not None, name
    evidence = session.scalar(select(models.Evidence).where(
        models.Evidence.case_id == result.case_id,
        models.Evidence.kind == "shared_scope_adapter",
    ))
    assessment = session.scalar(select(models.TriageAssessment).where(
        models.TriageAssessment.case_id == result.case_id
    ))
    if scope_available:
        assert shared["status"] == "answered"
        assert triage.input_json["scope_ratio"] == 0.0
        assert triage.output_json["status"] == "answered"
        assert assessment is not None and assessment.active_rank == 1
        assert evidence.value_json["values"]["scope_ratio"] == 0.0
        assert (
            evidence.value_json["values"]["shared_classification_confidence"]
            == shared["confidence"]
        )
    else:
        assert shared["status"] == "unknown"
        assert triage.input_json["scope_ratio"] is None
        assert triage.output_json["status"] == "unknown"
        assert triage.output_json["factors"]["missing_inputs"] == ["scope_ratio"]
        for name in ("score", "band", "active_rank", "percentile"):
            assert triage.output_json[name] is None
        assert assessment is None
        assert session.get(models.Case, result.case_id).triage_score is None
        assert evidence.value_json["values"]["status"] == "unknown"


@pytest.mark.parametrize("quality_mode,target_present", [
    ("real", True), ("sentinel", True), ("unreliable", True),
    ("sentinel", False), ("unknown", True),
])
def test_replay_consumes_quality_adapter_and_preserves_revenue_guard(
    session: Session, monkeypatch: pytest.MonkeyPatch,
    quality_mode: str, target_present: bool,
) -> None:
    event_time = seed_scenario(session)
    session.add(models.Tariff(
        name="Quality fixture tariff", customer_segment="residential", currency="JOD",
        jod_per_kwh=Decimal("0.12"), effective_from=event_time - timedelta(days=1),
        source="fixture:quality", is_synthetic=True,
    ))
    if not target_present:
        # The database disallows NULL; exercise the nullable reading-tool contract.
        read_window = investigation_module.get_reading_window

        def missing_target(data, database):
            output = read_window(data, database)
            return output.model_copy(update={"readings": [
                point.model_copy(update={"kwh": None})
                if point.timestamp == event_time else point
                for point in output.readings
            ]})

        monkeypatch.setattr(investigation_module, "get_reading_window", missing_target)
    session.flush()
    real_adapter = advanced.adapt_day3_quality_for_day5
    outputs = []

    def adapter(**kwargs):
        value = real_adapter(**kwargs)
        if quality_mode in {"sentinel", "unreliable"}:
            value = {**value, "data_confidence": 0.37,
                     "data_reliable": quality_mode != "unreliable"}
        elif quality_mode == "unknown":
            value = {"status": "unknown", "reason": "Fixture quality unavailable"}
        outputs.append(value)
        return value

    spy = Mock(side_effect=adapter)
    monkeypatch.setattr(advanced, "adapt_day3_quality_for_day5", spy)
    result = InvestigationService(session).replay(
        ReplayCommand(meter_id="M1", event_time=event_time)
    )
    traces = {row.tool_name: row for row in session.scalars(
        select(models.ToolExecution).where(models.ToolExecution.run_id == result.run_id)
    )}
    quality = traces["validate_reading_quality"].output_json
    spy.assert_called_once_with(
        quality_score=quality["quality_score"], reliable=quality["reliable"],
        quality_status=quality["status"],
    )
    adapted = outputs[0]
    triage = traces["calculate_triage_priority"]
    revenue = traces["estimate_revenue_at_risk"]
    assert triage.input_json["data_confidence"] == adapted.get("data_confidence")
    assert revenue.input_json["data_confidence"] == adapted.get("data_confidence")
    expected_reliable = (
        adapted["data_reliable"] and target_present
        if adapted["status"] == "success" else None
    )
    assert revenue.input_json["data_reliable"] is expected_reliable
    evidence = session.scalar(select(models.Evidence).where(
        models.Evidence.case_id == result.case_id, models.Evidence.kind == "quality_adapter",
    ))
    assert evidence.value_json["values"] == adapted
    if expected_reliable is True:
        assert revenue.output_json["status"] == "answered"
        assert revenue.output_json["confidence"] == adapted["data_confidence"]
    else:
        assert revenue.output_json["status"] == "unknown"
        assert session.scalar(select(func.count()).select_from(models.FinancialImpact)) == 0
    if quality_mode == "unknown":
        assert triage.output_json["status"] == "unknown"
        assert "data_confidence" in triage.output_json["factors"]["missing_inputs"]
        assert session.scalar(select(func.count()).select_from(models.TriageAssessment)) == 0
