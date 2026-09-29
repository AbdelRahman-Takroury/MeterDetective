from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.scenario_two_fixture import METER_IDS as SCENARIO_TWO_METERS
from app.services.scenario_one import METER_IDS as SCENARIO_ONE_METERS


def test_health_checks_database(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_status_degrades_safely_when_readiness_tables_are_missing(client: TestClient) -> None:
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["application"]["status"] == "ready"
    assert payload["database"]["status"] == "connected"
    assert payload["demonstration_dataset"]["status"] == "unavailable"
    assert payload["knowledge_base"]["status"] == "unavailable"
    assert payload["weather"]["status"] == "unavailable"
    assert payload["latest_investigation"]["status"] == "unavailable"
    assert payload["narrative_service"]["status"] in {
        "configured",
        "disabled",
        "unavailable",
    }
    assert "api_key" not in response.text.lower()
    assert "database_url" not in response.text.lower()


@pytest.fixture
def ready_status_client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            models.Asset.__table__,
            models.Meter.__table__,
            models.Case.__table__,
            models.Document.__table__,
            models.Evidence.__table__,
            models.AgentRun.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)
    with factory.begin() as db:
        db.add_all(
            models.Meter(id=meter_id, type="smart", status="active")
            for meter_id in (*SCENARIO_ONE_METERS, *SCENARIO_TWO_METERS)
        )
        db.add(models.Document(title="Guidance", license="Test license"))
        case = models.Case(title="Stored demonstration", status="resolved")
        db.add(case)
        db.flush()
        db.add(
            models.Evidence(
                case_id=case.id,
                kind="weather_context",
                source="Open-Meteo",
                value_json={"status": "answered", "from_cache": True},
                reliability=0.9,
            )
        )
        db.add(
            models.AgentRun(
                case_id=case.id,
                trigger={"source": "test"},
                status="succeeded",
                state_json={},
                started_at=datetime(2026, 9, 29, 10, tzinfo=UTC),
                ended_at=datetime(2026, 9, 29, 10, 1, tzinfo=UTC),
            )
        )

    def override_db() -> Iterator[Session]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_status_reports_ready_cached_and_stored_metadata(
    ready_status_client: TestClient,
) -> None:
    response = ready_status_client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["environment"] == "simulation"
    assert payload["demonstration_dataset"] | {"observed_at": None} == {
        "status": "ready",
        "detail": "Both deterministic demonstration scenarios are available.",
        "critical": True,
        "count": 6,
        "observed_at": None,
    }
    assert payload["knowledge_base"]["status"] == "ready"
    assert payload["knowledge_base"]["count"] == 1
    assert payload["weather"]["status"] == "cached"
    assert payload["latest_investigation"]["status"] == "ready"
