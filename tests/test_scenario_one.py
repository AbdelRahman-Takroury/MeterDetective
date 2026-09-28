"""Day 6 Scenario 1 is repeatable through reset, approval, repair, and verification."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services.scenario_one import METER_IDS, SCENARIO_ID


@pytest.fixture
def scenario_client() -> Iterator[tuple[TestClient, sessionmaker[Session], UUID]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        legacy = models.Case(title="Unrelated imported case", status="closed")
        session.add(legacy)
        session.flush()
        legacy_id = legacy.id

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            yield client, factory, legacy_id
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _recommendation(detail: dict) -> tuple[str, str]:
    recommendation = detail["recommendations"][-1]
    action = next(
        item for item in detail["actions"]
        if item["recommendation_id"] == recommendation["id"]
    )
    return recommendation["id"], action["id"]


def test_scenario_one_three_resets_rejection_repair_and_resolution(
    scenario_client: tuple[TestClient, sessionmaker[Session], UUID],
) -> None:
    client, factory, legacy_id = scenario_client

    first = client.post("/api/replay/reset")
    assert first.status_code == 200, first.text
    first_case = first.json()["case_id"]
    first_detail = client.get(f"/api/cases/{first_case}").json()
    assert first_detail["report_coverage"] == {
        "has_report": True,
        "legacy_or_imported": False,
        "present_question_ids": list(range(1, 19)),
        "missing_question_ids": [],
        "present_count": 18,
        "answered_count": 17,
    }
    assert first_detail["financial_impact"] is not None
    assert first_detail["triage_assessment"] is not None
    assert first_detail["scenario"]["id"] == SCENARIO_ID
    recommendation_id, action_id = _recommendation(first_detail)
    blocked = client.post(
        f"/api/actions/{action_id}/execute-simulation", json={"result": {}}
    )
    assert blocked.status_code == 403
    rejected = client.post(
        f"/api/recommendations/{recommendation_id}/reject",
        json={"decided_by": "scenario-test", "comment": "Exercise rejection gate"},
    )
    assert rejected.status_code == 200
    assert client.post(
        f"/api/actions/{action_id}/execute-simulation", json={"result": {}}
    ).status_code == 403

    second = client.post("/api/replay/reset")
    assert second.status_code == 200, second.text
    payload = second.json()
    detail = client.get(f"/api/cases/{payload['case_id']}").json()
    recommendation_id, action_id = _recommendation(detail)
    approved = client.post(
        f"/api/recommendations/{recommendation_id}/approve",
        json={"decided_by": "scenario-test", "comment": "Apply synthetic repair"},
    )
    assert approved.status_code == 200
    repaired = client.post(
        f"/api/actions/{action_id}/execute-simulation",
        json={"result": {"source": "scenario-test"}},
    )
    assert repaired.status_code == 200, repaired.text
    repair_result = repaired.json()["action"]["result_json"]
    assert repair_result["repair"] == "applied"
    assert repair_result["readings_inserted"] == 12
    verify = client.post(
        f"/api/cases/{payload['case_id']}/verify",
        json={
            "action_id": action_id,
            "window_start": repair_result["verification_window"]["start"],
            "window_end": repair_result["verification_window"]["end"],
        },
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["outcome"] == "recovered"
    assert verify.json()["case_status"] == "resolved"
    assert verify.json()["report_version"] > 1
    resolved = client.get(f"/api/cases/{payload['case_id']}").json()
    question_15 = next(
        item for item in resolved["latest_report"]["answers"]
        if item["question_id"] == 15
    )
    assert question_15["status"] == "answered"
    assert question_15["structured_values"]["outcome"] == "recovered"
    assert any(item["event_type"] == "simulated_repair_applied"
               for item in resolved["case_events"])

    third = client.post("/api/replay/reset")
    assert third.status_code == 200, third.text
    assert third.json()["case_id"] != payload["case_id"]
    with factory() as session:
        assert session.get(models.Case, legacy_id) is not None
        scenario_cases = session.scalar(
            select(func.count()).select_from(models.CaseMeter).where(
                models.CaseMeter.meter_id.in_(METER_IDS)
            )
        )
        assert scenario_cases == 1
        assert session.scalar(select(func.count()).select_from(models.Reading).where(
            models.Reading.meter_id.in_(METER_IDS),
            models.Reading.source == SCENARIO_ID,
        )) > 4_000


def test_reportless_case_is_explicitly_identified(
    scenario_client: tuple[TestClient, sessionmaker[Session], UUID],
) -> None:
    client, _factory, legacy_id = scenario_client
    detail = client.get(f"/api/cases/{legacy_id}")
    assert detail.status_code == 200
    coverage = detail.json()["report_coverage"]
    assert coverage["legacy_or_imported"] is True
    assert coverage["missing_question_ids"] == list(range(1, 19))
