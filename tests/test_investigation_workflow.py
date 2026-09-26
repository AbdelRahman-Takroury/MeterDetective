from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base
from app.db.repositories import HypothesisRepository
from app.db.session import get_db
from app.main import app
from app.services.investigation import InvestigationService, ReplayCommand
from app.tools.database import PrecedentInput, find_meter_precedents


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
    session.commit()
    return event_time


def test_replay_creates_one_inspectable_case_and_is_idempotent(session: Session) -> None:
    event_time = seed_scenario(session)
    service = InvestigationService(session)
    first = service.replay(ReplayCommand(meter_id="M1", event_time=event_time))
    session.commit()

    assert first.status == "case_created"
    assert first.case_id is not None
    assert first.report_id is not None
    assert first.tool_calls == 13
    assert session.scalar(select(func.count()).select_from(models.AgentRun)) == 1
    assert session.scalar(select(func.count()).select_from(models.ToolExecution)) == 13
    assert session.scalar(select(func.count()).select_from(models.Case)) == 1

    report = session.get(models.InvestigationReport, first.report_id)
    assert report is not None
    assert len(report.answers_json) == 18
    assert {item["question_id"] for item in report.answers_json} == set(range(1, 19))
    assert report.answers_json[14]["status"] == "pending_verification"
    assert all(item["status"] for item in report.answers_json)

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
            assert replay_body["tool_calls"] == 13
            case_id = replay_body["case_id"]

            detail = client.get(f"/api/cases/{case_id}")
            assert detail.status_code == 200
            detail_body = detail.json()
            assert len(detail_body["latest_report"]["answers"]) == 18
            assert len(detail_body["hypotheses"]) >= 3

            trace = client.get(f"/api/cases/{case_id}/trace")
            assert trace.status_code == 200
            tools = trace.json()["runs"][0]["tools"]
            assert len(tools) == 13
            assert all(tool["status"] == "succeeded" for tool in tools)
    finally:
        app.dependency_overrides.clear()
