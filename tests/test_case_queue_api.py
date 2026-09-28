"""Queue summaries use the latest report and preserve unavailable evidence."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.main import app


def test_queue_latest_report_ordering_and_missing_data():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        critical = models.Case(title="Critical meter", status="awaiting_approval",
                               priority_band="P1", active_rank=1)
        ordinary = models.Case(title="No report yet", status="investigating",
                               priority_band="P4")
        db.add_all([critical, ordinary])
        db.flush()
        db.add(models.Meter(id="M-queue", type="smart", status="active"))
        db.flush()
        db.add(models.CaseMeter(case_id=critical.id, meter_id="M-queue",
                                relationship="affected"))
        for version, base in [(1, 99), (2, 12)]:
            db.add(models.InvestigationReport(
                case_id=critical.id, version=version, status="investigating", completeness=1,
                answers_json=[
                    {"question_id": 1, "status": "answered",
                     "structured_values": {"anomaly_type": "drop"}},
                    {"question_id": 16, "status": "answered", "structured_values": {
                        "revenue_at_risk_jod": {"low": 1, "base": base, "high": 100}}},
                ],
            ))
        db.add(models.Recommendation(case_id=critical.id, action_type="meter_repair",
                                     rationale="Inspect", risk="high",
                                     status="pending_approval", created_at=datetime.now(UTC)))
        db.commit()

    def override():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            response = client.get("/api/triage/queue?limit=1")
            assert response.status_code == 200
            body = response.json()
            assert body["total"] == 1
            assert body["investigated_total"] == 1
            assert body["legacy_total"] == 1
            item = body["items"][0]
            assert item["priority_band"] == "P1"
            assert item["meter_ids"] == ["M-queue"]
            assert item["revenue_jod"]["base"] == 12
            assert item["report_version"] == 2
            assert item["answered_count"] == item["present_count"] == 2
            assert item["recommendation_status"] == "pending_approval"
            assert client.get("/api/triage/queue?offset=1").json()["items"] == []

            history = client.get("/api/triage/queue?view=history").json()
            assert history["total"] == 1
            second = history["items"][0]
            assert second["revenue_jod"] is None
            assert second["precedent_count"] is None
            assert second["report_version"] is None
            all_cases = client.get("/api/triage/queue?view=all").json()
            assert all_cases["total"] == 2
            assert len(all_cases["items"]) == 2
            assert client.get("/api/triage/queue?view=invalid").status_code == 422
            assert client.get("/api/triage/queue?limit=0").status_code == 422
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_dashboard_and_api_routes_coexist(client):
    response = client.get("/")
    assert response.status_code == 200
    assert '/static/app.js' in response.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/static/../.env").status_code == 404
