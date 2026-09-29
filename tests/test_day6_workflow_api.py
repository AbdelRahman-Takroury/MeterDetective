"""Recommendation, human-approval, and simulated-action workflow tests."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture
def workflow_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _record):  # noqa: ANN001
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            models.Asset.__table__,
            models.Meter.__table__,
            models.Case.__table__,
            models.CaseMeter.__table__,
            models.Evidence.__table__,
            models.Hypothesis.__table__,
            models.InvestigationReport.__table__,
            models.Tariff.__table__,
            models.FinancialImpact.__table__,
            models.TriageAssessment.__table__,
            models.Recommendation.__table__,
            models.Approval.__table__,
            models.Action.__table__,
            models.CaseEvent.__table__,
            models.AgentRun.__table__,
            models.ToolExecution.__table__,
            models.Event.__table__,
            models.InvestigationPlan.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        case = models.Case(title="Drop investigation", status="investigating")
        session.add(case)
        session.flush()
        answers = [
            {
                "question_id": question_id,
                "status": "pending_verification" if question_id == 15 else "unknown",
                "answer": "Verification is pending." if question_id == 15 else "Not assessed.",
                "structured_values": {},
                "confidence": 0,
                "supporting_evidence": [],
                "contradicting_evidence": [],
                "tools_used": [],
                "data_sources": [],
                "limitations": ["Fixture has no investigation evidence."],
                "fresh_as_of": None,
            }
            for question_id in range(1, 19)
        ]
        session.add(
            models.InvestigationReport(
                case_id=case.id,
                version=1,
                status="investigating",
                answers_json=answers,
                completeness=1,
            )
        )

    def override_db() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _case_id(factory: sessionmaker[Session]) -> str:
    with factory() as session:
        return str(session.scalar(select(models.Case.id)))


def _propose(client: TestClient, case_id: str) -> dict:
    response = client.post(
        f"/api/cases/{case_id}/recommendations",
        json={
            "action_type": "meter_repair",
            "rationale": "The isolated sustained drop warrants a simulated repair.",
            "risk": "high",
            "requires_approval": False,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_high_impact_recommendation_cannot_bypass_approval(
    workflow_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = workflow_client
    proposed = _propose(client, _case_id(factory))

    assert proposed["recommendation"]["requires_approval"] is True
    assert proposed["recommendation"]["status"] == "pending_approval"
    assert proposed["action"]["status"] == "awaiting_approval"
    blocked = client.post(
        f"/api/actions/{proposed['action']['id']}/execute-simulation",
        json={"result": {"repair": "applied"}},
    )
    assert blocked.status_code == 403


def test_approval_allows_one_simulation_and_records_audit_state(
    workflow_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = workflow_client
    case_id = _case_id(factory)
    proposed = _propose(client, case_id)
    recommendation_id = proposed["recommendation"]["id"]
    action_id = proposed["action"]["id"]

    approved = client.post(
        f"/api/recommendations/{recommendation_id}/approve",
        json={"decided_by": "operator@example.com", "comment": "Dispatch approved"},
    )
    assert approved.status_code == 200
    assert approved.json()["approval"]["decision"] == "approved"
    assert approved.json()["action"]["status"] == "ready"

    executed = client.post(
        f"/api/actions/{action_id}/execute-simulation",
        json={"result": {"repair": "applied", "simulation": True}},
    )
    assert executed.status_code == 200
    assert executed.json()["action"]["status"] == "completed"
    assert executed.json()["recommendation"]["status"] == "executed"
    assert (
        client.post(
            f"/api/actions/{action_id}/execute-simulation", json={"result": {}}
        ).status_code
        == 409
    )

    detail = client.get(f"/api/cases/{case_id}")
    assert detail.status_code == 200
    assert detail.json()["case"]["status"] == "pending_verification"
    assert detail.json()["recommendations"][0]["approval"]["decision"] == "approved"
    assert detail.json()["actions"][0]["result"]["repair"] == "applied"
    assert [event["event_type"] for event in detail.json()["case_events"]] == [
        "recommendation_created",
        "approval_decided",
        "action_completed",
    ]


def test_rejection_is_final_and_prevents_execution(
    workflow_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = workflow_client
    proposed = _propose(client, _case_id(factory))
    recommendation_id = proposed["recommendation"]["id"]
    action_id = proposed["action"]["id"]

    rejected = client.post(
        f"/api/recommendations/{recommendation_id}/reject",
        json={"decided_by": "operator@example.com", "comment": "Monitor first"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["action"]["status"] == "rejected"
    assert (
        client.post(
            f"/api/actions/{action_id}/execute-simulation", json={"result": {}}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/recommendations/{recommendation_id}/approve",
            json={"decided_by": "another operator"},
        ).status_code
        == 409
    )


def test_low_risk_non_intervention_can_be_ready_without_approval(
    workflow_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = workflow_client
    response = client.post(
        f"/api/cases/{_case_id(factory)}/recommendations",
        json={
            "action_type": "continue_monitoring",
            "rationale": "Confidence is too low for intervention.",
            "risk": "low",
            "requires_approval": False,
        },
    )
    assert response.status_code == 201
    assert response.json()["recommendation"]["requires_approval"] is False
    assert response.json()["action"]["status"] == "ready"


def test_unclassified_low_risk_action_defaults_to_human_approval(
    workflow_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = workflow_client
    response = client.post(
        f"/api/cases/{_case_id(factory)}/recommendations",
        json={
            "action_type": "disconnect_meter",
            "rationale": "An unclassified intervention must not bypass the approval gate.",
            "risk": "low",
            "requires_approval": False,
        },
    )
    assert response.status_code == 201
    assert response.json()["recommendation"]["requires_approval"] is True
    assert response.json()["action"]["status"] == "awaiting_approval"


def test_action_without_recommendation_or_matching_action_type_cannot_execute(
    workflow_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = workflow_client
    case_id = _case_id(factory)
    with factory.begin() as session:
        unlinked = models.Action(
            case_id=UUID(case_id),
            action_type="meter_repair",
            status="ready",
            result_json={},
        )
        session.add(unlinked)
        session.flush()
        unlinked_id = str(unlinked.id)
    assert (
        client.post(f"/api/actions/{unlinked_id}/execute-simulation", json={"result": {}})
        .status_code
        == 403
    )

    proposed = client.post(
        f"/api/cases/{case_id}/recommendations",
        json={
            "action_type": "continue_monitoring",
            "rationale": "Use a safe non-intervention action for an integrity test.",
            "risk": "low",
            "requires_approval": False,
        },
    ).json()
    with factory.begin() as session:
        action = session.get(models.Action, UUID(proposed["action"]["id"]))
        assert action is not None
        action.action_type = "meter_repair"
    assert (
        client.post(
            f"/api/actions/{proposed['action']['id']}/execute-simulation", json={"result": {}}
        ).status_code
        == 409
    )
