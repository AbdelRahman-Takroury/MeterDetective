"""Request correlation and JSON logging without request-body or credential data."""

import json
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger("meterdetective.api")


def choose_request_id(value: str | None) -> str:
    """Preserve a safe caller ID or generate one; never log arbitrary header text."""
    return value if value is not None and _REQUEST_ID_PATTERN.fullmatch(value) else str(uuid4())


def current_request_id() -> str | None:
    return _request_id.get()


@contextmanager
def request_id_scope(value: str) -> Iterator[None]:
    token = _request_id.set(value)
    try:
        yield
    finally:
        _request_id.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str | int | float] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        request_id = current_request_id()
        if request_id is not None:
            payload["request_id"] = request_id
        for field in ("method", "path", "status_code", "duration_ms", "error_type"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging() -> None:
    """Install one structured handler even when the module is imported repeatedly."""
    if not any(handler.name == "meterdetective_json" for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.set_name("meterdetective_json")
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.disabled = False
    logger.propagate = False
