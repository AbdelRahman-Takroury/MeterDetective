"""Request correlation and safe JSON request-log behavior."""

import io
import json
import logging
from collections.abc import Iterator
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.observability import JsonFormatter, current_request_id, logger
from app.db.session import get_db
from app.main import app


def _capture_logs() -> tuple[io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return stream, handler


def test_caller_and_generated_request_ids_are_returned_and_logged(client: TestClient) -> None:
    stream, handler = _capture_logs()
    try:
        supplied = client.get("/api/health?token=do-not-log", headers={"X-Request-ID": "trace-123"})
        generated = client.get("/api/does-not-exist", headers={"X-Request-ID": "bad id"})
        private = client.get("/api/meters/M-private", headers={"X-Request-ID": "trace-private"})
    finally:
        logger.removeHandler(handler)

    assert supplied.status_code == 200
    assert supplied.headers["X-Request-ID"] == "trace-123"
    assert generated.status_code == 404
    assert private.status_code == 503
    generated_id = generated.headers["X-Request-ID"]
    assert str(UUID(generated_id)) == generated_id
    assert current_request_id() is None

    entries = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(entries) == 3
    assert entries[0]["request_id"] == "trace-123"
    assert entries[0]["event"] == "request_completed"
    assert entries[0]["method"] == "GET"
    assert entries[0]["path"] == "/api/health"
    assert entries[0]["status_code"] == 200
    assert entries[0]["duration_ms"] >= 0
    assert entries[1]["request_id"] == generated_id
    assert entries[1]["status_code"] == 404
    assert entries[2]["path"] == "/api/meters/{meter_id}"
    assert "do-not-log" not in stream.getvalue()
    assert "bad id" not in stream.getvalue()
    assert "M-private" not in stream.getvalue()


def test_unhandled_error_has_request_id_and_safe_structured_log() -> None:
    def broken_db() -> Iterator[Session]:
        raise RuntimeError("secret connection detail")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken_db
    stream, handler = _capture_logs()
    try:
        with TestClient(app) as client:
            response = client.get("/api/meters", headers={"X-Request-ID": "failure-1"})
    finally:
        logger.removeHandler(handler)
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "failure-1"
    assert response.json() == {"detail": "Internal server error"}
    entries = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [entry["event"] for entry in entries] == ["request_failed", "request_completed"]
    assert entries[0]["error_type"] == "RuntimeError"
    assert all(entry["request_id"] == "failure-1" for entry in entries)
    assert "secret connection detail" not in stream.getvalue()
