"""Reading arrival, automatic dispatch, and evidence-driven case resumption."""

from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services import investigation
from app.services.plans import update_plan
from app.services.reading_events import IncomingReading, ReadingEventService
from app.services.scenario_one import ScenarioOneService, scenario_registry
from app.services.scenario_two import EVENT_TIME, METER_IDS, SCENARIO_ID, ScenarioTwoService
from app.tools.database import PrecedentInput, find_meter_precedents


@pytest.fixture
def workspace() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    def database():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def play(client, stage):
    response = client.post("/api/replay/scenario-2/replay", json={"stage": stage})
    assert response.status_code == 200, response.text
    return response.json()


def test_reset_normal_initial_followup_and_redelivery(workspace):
    client, factory = workspace
    assert client.post("/api/replay/scenario-2/reset").status_code == 200
    normal = play(client, "normal")
    assert all(item["status"] == "normal" and item["case_id"] is None for item in normal)
    initial = play(client, "initial")
    created = [item for item in initial if item["case_id"]]
    assert len(created) == 1
    case_id = created[0]["case_id"]
    before = client.get(f"/api/cases/{case_id}").json()
    first_report = before["latest_report"]["id"]
    assert len(before["plans"]) == 1
    plan_one = before["plans"][0]
    assert plan_one["scope"] == "local"
    old_action = before["actions"][0]["id"]
    old_recommendation = before["recommendations"][0]
    followup = play(client, "shared")
    assert {item["case_id"] for item in followup} == {case_id}
    assert all(item["status"] == "case_updated" for item in followup)
    detail = client.get(f"/api/cases/{case_id}").json()
    assert [(plan["version"], plan["status"], plan["scope"]) for plan in detail["plans"]] == [
        (1, "invalidated", "local"), (2, "active", "shared"),
    ]
    for field in ("id", "goal", "steps", "evidence_ids", "created_at", "source_report_id"):
        assert detail["plans"][0][field] == plan_one[field]
    changes = [item for item in detail["case_events"] if item["event_type"] == "plan_invalidated"]
    assert len(changes) == 1
    change = changes[0]["details"]
    assert change["old_plan_id"] == plan_one["id"]
    assert change["new_plan_id"] == detail["plans"][1]["id"]
    assert change["contradicting_evidence_ids"]
    assert set(change["contradicting_evidence_ids"]) <= {item["id"] for item in detail["evidence"]}
    assert set(detail["meter_ids"]) == set(METER_IDS)
    assert detail["latest_report"]["id"] != first_report
    assert detail["report_coverage"]["present_count"] == 18
    assert detail["recommendations"][-1]["status"] == "pending_approval"
    assert detail["recommendations"][-1]["action_type"] == "transformer_inspection"
    replacement = detail["recommendations"][-1]
    preserved = detail["recommendations"][0]
    assert preserved["id"] == old_recommendation["id"]
    assert preserved["status"] == "superseded"
    assert preserved["superseded_by_id"] == replacement["id"]
    assert preserved["superseded_at"] is not None
    assert replacement["plan_id"] == detail["plans"][1]["id"]
    assert replacement["source_report_id"] is not None
    superseded = [
        item for item in detail["case_events"]
        if item["event_type"] == "recommendation_superseded"
    ]
    assert len(superseded) == 1
    assert superseded[0]["details"]["old_recommendation_id"] == preserved["id"]
    assert superseded[0]["details"]["new_recommendation_id"] == replacement["id"]
    trace = client.get(f"/api/cases/{case_id}/trace").json()
    proposal_runs = 0
    for run in trace["runs"]:
        names = [item["name"] for item in run["tools"]]
        assert names.index("detect_anomaly") < names.index("create_case_and_record_investigation")
        assert names[-1] in {
            "create_case_and_record_investigation", "propose_action_for_approval",
        }
        proposal_runs += names.count("propose_action_for_approval")
        precedent = next(item for item in run["tools"] if item["name"] == "find_meter_precedents")
        output = precedent["output"]
        assert all(item["case_id"] != case_id for item in (
            output["exact_meter_cases"] + output["similar_system_cases"]
        ))
        triage = next(item for item in run["tools"] if item["name"] == "calculate_triage_priority")
        assert triage["input"]["recurrence_score"] == 0
        revenue = next(item for item in run["tools"] if item["name"] == "estimate_revenue_at_risk")
        assert revenue["input"]["observed_kwh"] in (3.0, 3.12, 3.24)
    assert proposal_runs == 2
    assert any(item["event_type"] == "investigation_resumed" for item in detail["case_events"])
    assert len(detail["hypotheses"]) == 3
    assert all(len(item["update_history"]) == 4 for item in detail["hypotheses"])
    assert client.post(f"/api/actions/{old_action}/execute-simulation", json={
        "result": {"simulation": True}
    }).status_code in (403, 409)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(models.Case)) == 1
        assert session.scalar(select(func.count()).select_from(models.Event)) == 9
        assert all(e.status == "completed" for e in session.scalars(select(models.Event)))
        assert session.get(models.InvestigationReport, UUID(first_report)) is not None
        counts = {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (
                models.InvestigationReport, models.Recommendation, models.Action,
                models.CaseEvent, models.InvestigationPlan,
            )
        }
    duplicate_followup = play(client, "shared")
    assert all(item["duplicate"] for item in duplicate_followup)
    for original, duplicate in zip(followup, duplicate_followup, strict=True):
        assert {key: value for key, value in duplicate.items() if key != "duplicate"} == {
            key: value for key, value in original.items() if key != "duplicate"
        }
    after = client.get(f"/api/cases/{case_id}").json()
    assert after["plans"] == detail["plans"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(models.Event)) == 9
        assert counts == {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (
                models.InvestigationReport, models.Recommendation, models.Action,
                models.CaseEvent, models.InvestigationPlan,
            )
        }


def test_plan_change_preserves_approved_history_and_publishes_revised_workflow(workspace):
    client, _ = workspace
    assert client.post("/api/replay/scenario-2/reset").status_code == 200
    initial = play(client, "initial")
    case_id = next(item["case_id"] for item in initial if item["case_id"])
    before = client.get(f"/api/cases/{case_id}").json()
    old_recommendation = before["recommendations"][0]
    old_action = before["actions"][0]

    approved = client.post(
        f"/api/recommendations/{old_recommendation['id']}/approve",
        json={"decided_by": "demo.operator", "comment": "Approved before new evidence"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["action"]["status"] == "ready"

    shared = play(client, "shared")
    assert all(item["case_id"] == case_id for item in shared)
    detail = client.get(f"/api/cases/{case_id}").json()
    assert len(detail["recommendations"]) == 2
    old, revised = detail["recommendations"]
    assert old["status"] == "superseded"
    assert old["approval"]["decision"] == "approved"
    assert old["superseded_by_id"] == revised["id"]
    assert revised["status"] == "pending_approval"
    assert revised["action_type"] == "transformer_inspection"
    assert revised["plan_id"] == detail["plans"][-1]["id"]
    assert next(item for item in detail["actions"] if item["id"] == old_action["id"])[
        "status"
    ] == "superseded"
    answers = {
        item["question_id"]: item for item in detail["latest_report"]["answers"]
    }
    assert answers[13]["structured_values"]["recommendation_id"] == revised["id"]
    assert answers[13]["structured_values"]["action_type"] == "transformer_inspection"
    assert answers[14]["structured_values"]["approval_status"] == "pending"
    blocked = client.post(
        f"/api/actions/{old_action['id']}/execute-simulation",
        json={"result": {"simulation": True}},
    )
    assert blocked.status_code in (403, 409)


def test_reset_isolated_from_scenario_one_and_imports(workspace):
    client, factory = workspace
    with factory.begin() as session:
        imported = models.Case(title="Historical reference", status="closed")
        session.add(imported)
        one, _ = ScenarioOneService(session).reset_and_run()
        session.flush()
        imported_id = imported.id
    for _ in range(2):
        assert client.post("/api/replay/scenario-2/reset").status_code == 200
        play(client, "initial")
        play(client, "shared")
    assert client.post("/api/replay/scenario-2/reset").status_code == 200
    with factory() as session:
        assert session.get(models.Case, imported_id)
        assert session.get(models.Case, one.case_id)
        assert session.scalar(select(func.count()).select_from(models.Case)) == 2
        assert not list(session.scalars(select(models.Event).where(
            models.Event.payload_json["meter_id"].as_string().in_(METER_IDS)
        )))


def test_replay_requires_reset_and_initial_stage(workspace):
    client, _ = workspace
    response = client.post("/api/replay/scenario-2/replay", json={"stage": "initial"})
    assert response.status_code == 422
    client.post("/api/replay/scenario-2/reset")
    assert client.post("/api/replay/scenario-2/replay", json={"stage": "shared"}).status_code == 422
    response = client.post("/api/replay/scenario-2/replay", json={"stage": "invented"})
    assert response.status_code == 422


def test_invalid_reading_batch_rolls_back_and_rejects_naive_time(workspace):
    client, factory = workspace
    client.post("/api/replay/scenario-2/reset")
    reading = {
        "meter_id": METER_IDS[0], "timestamp": EVENT_TIME.isoformat(),
        "kwh": 3, "source": SCENARIO_ID,
    }
    response = client.post("/api/replay/readings", json={"readings": [
        reading, {**reading, "kwh": 7},
    ]})
    assert response.status_code == 422
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(models.Event)) == 0
        assert session.scalar(select(models.Reading).where(
            models.Reading.meter_id == METER_IDS[0], models.Reading.timestamp == EVENT_TIME,
        )) is None
    reading["timestamp"] = "2026-09-20T12:00:00"
    assert client.post("/api/replay/readings", json={"readings": [reading]}).status_code == 422


@pytest.mark.parametrize("hours,close_case", [(0.5, False), (5, False), (0.5, True)])
def test_generic_arrival_matches_exact_meter_but_not_closed_or_old_case(
    workspace, hours, close_case,
):
    _, factory = workspace
    with factory.begin() as session:
        scenario = ScenarioTwoService(session)
        scenario.reset()
        original = next(result for result in scenario.replay_stage("initial") if result.case_id)
        if close_case:
            session.get(models.Case, original.case_id).status = "closed"
        result = ReadingEventService(session, scenario_registry()).receive([
            IncomingReading(
                meter_id=METER_IDS[0], timestamp=EVENT_TIME + timedelta(hours=hours),
                kwh=3, source="independent-reading-source",
            )
        ])[0]
        assert result.case_id is not None
        assert (result.case_id == original.case_id) == (hours == 0.5 and not close_case)


def test_public_reading_api_stores_and_routes_without_scenario_commands(workspace, monkeypatch):
    client, factory = workspace
    monkeypatch.setattr(investigation, "build_registry", scenario_registry)
    client.post("/api/replay/scenario-2/reset")
    reading = {
        "meter_id": METER_IDS[0], "timestamp": EVENT_TIME.isoformat(),
        "kwh": 3, "source": "meter-upload",
    }
    created = client.post("/api/replay/readings", json={"readings": [reading]})
    assert created.status_code == 200, created.text
    assert created.json()[0]["status"] == "case_created"
    duplicate = client.post("/api/replay/readings", json={"readings": [reading]})
    assert duplicate.json()[0]["duplicate"]
    assert duplicate.json()[0]["case_id"] == created.json()[0]["case_id"]
    conflict = client.post("/api/replay/readings", json={"readings": [{**reading, "kwh": 8}]})
    assert conflict.status_code == 422
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(models.Event)) == 1


def test_failed_investigation_keeps_reading_event_without_partial_case(workspace):
    _, factory = workspace
    with factory.begin() as session:
        ScenarioTwoService(session).reset()
        registry = scenario_registry()

        def fail(_data, _session):
            raise ValueError("Deliberate test failure after the case has been recorded")

        registry._tools["propose_action_for_approval"] = fail
        result = ReadingEventService(session, registry).receive([
            IncomingReading(meter_id=METER_IDS[0], timestamp=EVENT_TIME,
                            kwh=3, source=SCENARIO_ID),
        ])[0]
        assert result.status == "failed"
        assert session.get(models.Event, result.event_id).status == "failed"
        assert session.get(models.AgentRun, result.run_id).status == "failed"
        failed = session.scalar(select(models.ToolExecution).where(
            models.ToolExecution.run_id == result.run_id,
            models.ToolExecution.status == "failed",
        ))
        assert failed.tool_name == "propose_action_for_approval"
        assert session.scalar(select(func.count()).select_from(models.Case)) == 0
        assert session.scalar(select(models.Reading).where(
            models.Reading.meter_id == METER_IDS[0], models.Reading.timestamp == EVENT_TIME,
        )) is not None


def test_unrelated_transformer_and_normal_followup_do_not_resume_case(workspace):
    _, factory = workspace
    with factory.begin() as session:
        ScenarioTwoService(session).reset()
        initial = ScenarioTwoService(session).replay_stage("initial")[0]
        original = session.get(models.Case, initial.case_id)
        before = session.scalar(select(func.count()).select_from(models.InvestigationReport).where(
            models.InvestigationReport.case_id == original.id,
        ))
        normal = ReadingEventService(session, scenario_registry()).receive([
            IncomingReading(meter_id=METER_IDS[0], timestamp=EVENT_TIME + timedelta(minutes=30),
                            kwh=10, source=SCENARIO_ID),
        ])[0]
        assert normal.status == "normal"
        assert normal.case_id is None
        assert session.scalar(select(func.count()).select_from(models.InvestigationReport).where(
            models.InvestigationReport.case_id == original.id,
        )) == before
        ScenarioOneService(session)._seed_anomaly()
        other = ReadingEventService(session, scenario_registry()).receive([
            IncomingReading(meter_id="SC1-M1", timestamp=ScenarioOneService.event_time,
                            kwh=3, source="scenario-1-local-drop"),
        ])[0]
        assert other.case_id != original.id


def test_failed_resumption_preserves_prior_case_report_and_approval(workspace):
    _, factory = workspace
    with factory.begin() as session:
        ScenarioTwoService(session).reset()
        original = ScenarioTwoService(session).replay_stage("initial")[0]
        report_count = session.scalar(select(func.count()).select_from(models.InvestigationReport))
        recommendation = session.scalar(select(models.Recommendation).where(
            models.Recommendation.case_id == original.case_id,
        ))
        registry = scenario_registry()

        def fail(_data, _session):
            raise ValueError("Test proposal failure during resumption")

        registry._tools["propose_action_for_approval"] = fail
        results = ReadingEventService(session, registry).receive([
            IncomingReading(
                meter_id=meter_id,
                timestamp=EVENT_TIME + timedelta(minutes=30),
                kwh=kwh,
                source=SCENARIO_ID,
            )
            for meter_id, kwh in zip(METER_IDS, (3.0, 3.12, 3.24), strict=True)
        ])
        result = results[0]
        assert all(item.status == "failed" for item in results)
        assert result.status == "failed"
        assert session.get(models.AgentRun, result.run_id).case_id == original.case_id
        assert recommendation.status == "pending_approval"
        plans = list(session.scalars(select(models.InvestigationPlan)))
        assert len(plans) == 1 and plans[0].status == "active"
        assert session.scalar(select(func.count()).select_from(models.InvestigationReport)) == (
            report_count
        )
        traces = list(session.scalars(select(models.ToolExecution).where(
            models.ToolExecution.run_id == result.run_id,
        )))
        assert traces and all(trace.input_json["run_id"] == str(result.run_id) for trace in traces)


@pytest.mark.parametrize("target_kwh,should_resume", [(3.0, True), (10.0, False)])
def test_peer_arriving_separately_requires_current_shared_evidence(
    workspace, target_kwh, should_resume,
):
    _, factory = workspace
    with factory.begin() as session:
        scenario = ScenarioTwoService(session)
        scenario.reset()
        initial = scenario.replay_stage("initial")[0]
        service = ReadingEventService(session, scenario_registry())
        timestamp = EVENT_TIME + timedelta(minutes=30)
        service.receive([IncomingReading(
            meter_id=METER_IDS[0], timestamp=timestamp, kwh=target_kwh, source=SCENARIO_ID,
        )])
        peer_result = service.receive([IncomingReading(
            meter_id=METER_IDS[1], timestamp=timestamp, kwh=3.12, source=SCENARIO_ID,
        )])[0]
        assert peer_result.case_id is not None
        assert (peer_result.case_id == initial.case_id) == should_resume


@pytest.mark.parametrize("change", ["unchanged", "unknown", "unreliable"])
def test_plan_is_not_invalidated_without_reliable_contradiction(workspace, change):
    _, factory = workspace
    with factory.begin() as session:
        scenario = ScenarioTwoService(session)
        scenario.reset()
        result = scenario.replay_stage("initial")[0]
        plan = session.scalar(select(models.InvestigationPlan))
        rows = list(session.scalars(select(models.Evidence).where(
            models.Evidence.case_id == result.case_id
        )))
        new_ids = []
        for row in rows:
            value = dict(row.value_json)
            if row.kind == "shared_incident":
                if change == "unknown":
                    value.update(status="unknown", incident_type="unknown")
                elif change == "unreliable":
                    value.update(status="answered", incident_type="shared")
            if change == "unreliable" and row.kind == "data_quality":
                value["reliable"] = False
            copy = models.Evidence(case_id=result.case_id, kind=row.kind, source=row.source,
                                   value_json=value, reliability=row.reliability)
            session.add(copy)
            session.flush()
            new_ids.append(copy.id)
        returned = update_plan(
            session, case_id=result.case_id, report_id=result.report_id,
            event_id=result.event_id, run_id=result.run_id, evidence_ids=new_ids,
        )
        assert returned.id == plan.id and returned.version == 1 and returned.status == "active"
        assert not list(session.scalars(select(models.CaseEvent).where(
            models.CaseEvent.event_type == "plan_invalidated"
        )))


def test_current_case_excluded_before_precedent_limit_without_losing_history(workspace):
    _, factory = workspace
    with factory.begin() as session:
        scenario = ScenarioTwoService(session)
        scenario.reset()
        result = scenario.replay_stage("initial")[0]
        historical = models.Case(title="Previous resolved meter issue", status="resolved")
        session.add(historical)
        session.flush()
        session.add_all([models.CaseMeter(case_id=historical.id, meter_id=meter_id,
                                         relationship="affected") for meter_id in METER_IDS])
        session.flush()
        output = find_meter_precedents(PrecedentInput(
            run_id=result.run_id, meter_id=METER_IDS[0], exclude_case_id=result.case_id, limit=1,
        ), session)
        assert [item.case_id for item in output.exact_meter_cases] == [historical.id]
        assert output.similar_system_cases == []


def test_failed_plan_change_rolls_back_invalidation_and_new_version(workspace, monkeypatch):
    client, factory = workspace
    client.post("/api/replay/scenario-2/reset")
    initial = play(client, "initial")[0]

    def fail_after_plan(*args, **kwargs):
        update_plan(*args, **kwargs)
        raise investigation.InvestigationFailure("Test failure after plan persistence")

    monkeypatch.setattr(investigation, "update_plan", fail_after_plan)
    results = play(client, "shared")
    assert all(item["status"] == "failed" for item in results)
    with factory() as session:
        plans = list(session.scalars(select(models.InvestigationPlan)))
        assert len(plans) == 1
        assert plans[0].status == "active" and plans[0].invalidated_at is None
        assert plans[0].case_id == UUID(initial["case_id"])
        assert not list(session.scalars(select(models.CaseEvent).where(
            models.CaseEvent.event_type == "plan_invalidated"
        )))
