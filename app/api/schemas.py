"""Public response shapes for the initial read APIs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OrmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MeterSummary(OrmResponse):
    id: str
    transformer_id: UUID | None
    location: str | None
    type: str
    status: str
    customer_segment: str | None
    has_solar: bool | None
    has_ev: bool | None


class MeterDetail(MeterSummary):
    metadata_source: str | None
    metadata_json: dict[str, Any]


class MeterListResponse(BaseModel):
    items: list[MeterSummary]
    offset: int
    limit: int


class ReadingResponse(OrmResponse):
    id: UUID
    meter_id: str
    timestamp: datetime
    kwh: float
    quality_flag: str
    source: str


class ReadingWindowResponse(BaseModel):
    meter_id: str
    start: datetime
    end: datetime
    items: list[ReadingResponse]
    limit: int


class CaseSummary(OrmResponse):
    id: UUID
    title: str
    status: str
    triage_score: float | None
    priority_band: str | None
    active_rank: int | None
    confidence: float | None
    assigned_to: str | None
    opened_at: datetime
    updated_at: datetime


class CaseListResponse(BaseModel):
    items: list[CaseSummary]
    offset: int
    limit: int
