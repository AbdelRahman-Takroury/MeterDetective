"""Contract and error tests for the first meter/reading/case endpoints."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.seed import TABLE_KEYS, import_manifest

AT = datetime(2012, 5, 1, tzinfo=UTC)


@pytest.fixture
def populated_client() -> Iterator[TestClient]:
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
            models.Reading.__table__,
            models.Case.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        transformer = models.Asset(asset_type="transformer", name="TX_1")
        session.add(transformer)
        session.flush()
        session.add_all(
            [
                models.Meter(
                    id="M-1", transformer_id=transformer.id, type="residential", status="active"
                ),
                models.Meter(
                    id="M-2", transformer_id=transformer.id, type="residential", status="active"
                ),
            ]
        )
        session.add_all(
            [
                models.Reading(meter_id="M-1", timestamp=AT, kwh=1.5, source="fixture"),
                models.Reading(
                    meter_id="M-1",
                    timestamp=AT + timedelta(minutes=30),
                    kwh=2.5,
                    source="fixture",
                ),
                models.Reading(meter_id="M-2", timestamp=AT, kwh=3, source="fixture"),
            ]
        )
        session.add_all(
            [
                models.Case(title="Open investigation", status="open", opened_at=AT),
                models.Case(
                    title="Historical investigation",
                    status="closed",
                    opened_at=AT - timedelta(days=1),
                ),
            ]
        )

    def override_db() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_meter_list_detail_and_pagination(populated_client: TestClient) -> None:
    response = populated_client.get("/api/meters", params={"limit": 1, "offset": 1})
    assert response.status_code == 200
    assert response.json()["limit"] == 1
    assert [row["id"] for row in response.json()["items"]] == ["M-2"]
    assert response.json()["items"][0]["transformer_id"] is not None

    detail = populated_client.get("/api/meters/M-1")
    assert detail.status_code == 200
    assert detail.json()["id"] == "M-1"
    assert detail.json()["metadata_json"] == {}
    assert populated_client.get("/api/meters/missing").json() == {"detail": "Meter not found"}
    assert populated_client.get("/api/meters/missing").status_code == 404
    assert populated_client.get("/api/meters", params={"limit": 0}).status_code == 422


def test_reading_window_is_ordered_half_open_and_validated(populated_client: TestClient) -> None:
    params = {"start": AT.isoformat(), "end": (AT + timedelta(minutes=30)).isoformat()}
    response = populated_client.get("/api/meters/M-1/readings", params=params)
    assert response.status_code == 200
    assert response.json()["meter_id"] == "M-1"
    assert [row["kwh"] for row in response.json()["items"]] == [1.5]
    assert response.json()["items"][0]["quality_flag"] == "valid"

    full = populated_client.get(
        "/api/meters/M-1/readings",
        params={"start": AT.isoformat(), "end": (AT + timedelta(hours=1)).isoformat()},
    )
    assert [row["kwh"] for row in full.json()["items"]] == [1.5, 2.5]
    assert populated_client.get("/api/meters/missing/readings", params=params).status_code == 404
    assert (
        populated_client.get(
            "/api/meters/M-1/readings", params={"start": AT.isoformat(), "end": AT.isoformat()}
        ).status_code
        == 422
    )
    assert (
        populated_client.get(
            "/api/meters/M-1/readings",
            params={"start": "2012-05-01T00:00:00", "end": params["end"]},
        ).status_code
        == 422
    )


def test_case_list_filter_and_openapi_contract(populated_client: TestClient) -> None:
    response = populated_client.get("/api/cases", params={"status": "open"})
    assert response.status_code == 200
    assert [row["title"] for row in response.json()["items"]] == ["Open investigation"]
    assert response.json()["items"][0]["status"] == "open"
    assert populated_client.get("/api/cases", params={"offset": -1}).status_code == 422
    assert populated_client.get("/api/cases", params={"status": ""}).status_code == 422

    paths = populated_client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/meters",
        "/api/meters/{meter_id}",
        "/api/meters/{meter_id}/readings",
        "/api/cases",
    ):
        response = paths[path]["get"]["responses"]["200"]
        assert response["content"]["application/json"]["schema"]["$ref"]


def test_database_failure_returns_sanitized_error() -> None:
    def broken_db() -> Iterator[Session]:
        raise OperationalError("SELECT secret", {}, Exception("driver details"))
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/meters")
        assert response.status_code == 503
        assert response.json() == {"detail": "Database connection unavailable"}
    finally:
        app.dependency_overrides.clear()


def test_seeded_records_are_queryable_through_api() -> None:
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
        tables=[Base.metadata.tables[name] for name in TABLE_KEYS],
    )
    with engine.begin() as connection:
        import_manifest(connection, Path("data/seed/day2b.json"))
    factory = sessionmaker(bind=engine)

    def override_db() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            meters = client.get("/api/meters")
            cases = client.get("/api/cases")
            assert meters.status_code == 200
            assert len(meters.json()["items"]) == 3
            assert cases.status_code == 200
            assert len(cases.json()["items"]) == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
