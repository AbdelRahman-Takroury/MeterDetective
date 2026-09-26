from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class EvidenceReference(ContractModel):
    evidence_id: str | None = None
    source: str
    kind: str
    observed_at: datetime | None = None
    reliability: float = Field(ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
