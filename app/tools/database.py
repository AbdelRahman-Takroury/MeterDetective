"""Database-facing tools for profiles, readings, and precedents."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.common import EvidenceReference
from app.contracts.tool import ToolInput, ToolOutput
from app.db import models
from app.db.repositories import HistoryRepository, MeterRepository, ReadingRepository
from app.tools.analytics import ReadingPoint


class MeterProfileInput(ToolInput):
    meter_id: str


class MeterProfileOutput(ToolOutput):
    meter_id: str
    transformer_id: UUID | None
    location: str | None
    meter_type: str
    status: str
    customer_segment: str | None
    has_solar: bool | None
    has_ev: bool | None
    metadata_source: str | None


def get_meter_profile(data: MeterProfileInput, session: Session) -> MeterProfileOutput:
    meter = MeterRepository(session).get(data.meter_id)
    if meter is None:
        raise ValueError("Meter not found")
    evidence = EvidenceReference(
        source="database.meters",
        kind="meter_profile",
        reliability=1,
        metadata={"meter_id": meter.id, "metadata_source": meter.metadata_source},
    )
    return MeterProfileOutput(
        meter_id=meter.id,
        transformer_id=meter.transformer_id,
        location=meter.location,
        meter_type=meter.type,
        status=meter.status,
        customer_segment=meter.customer_segment,
        has_solar=meter.has_solar,
        has_ev=meter.has_ev,
        metadata_source=meter.metadata_source,
        evidence=[evidence],
    )


class ReadingWindowInput(ToolInput):
    meter_id: str
    start: datetime
    end: datetime
    limit: int = Field(default=10_000, ge=1, le=10_000)


class ReadingWindowOutput(ToolOutput):
    meter_id: str
    start: datetime
    end: datetime
    readings: list[ReadingPoint]


def get_reading_window(data: ReadingWindowInput, session: Session) -> ReadingWindowOutput:
    if MeterRepository(session).get(data.meter_id) is None:
        raise ValueError("Meter not found")
    rows = ReadingRepository(session).window(
        data.meter_id, data.start, data.end, limit=data.limit
    )
    readings = [
        ReadingPoint(
            timestamp=(
                row.timestamp.replace(tzinfo=UTC)
                if row.timestamp.tzinfo is None
                else row.timestamp.astimezone(UTC)
            ),
            kwh=row.kwh,
            quality_flag=row.quality_flag,
        )
        for row in rows
    ]
    observed_at = readings[-1].timestamp if readings else None
    return ReadingWindowOutput(
        meter_id=data.meter_id,
        start=data.start,
        end=data.end,
        readings=readings,
        evidence=[
            EvidenceReference(
                source="database.readings",
                kind="reading_window",
                observed_at=observed_at,
                reliability=1,
                metadata={"meter_id": data.meter_id, "count": len(readings)},
            )
        ],
        warnings=[] if readings else ["No readings found in the requested window."],
    )


class PrecedentInput(ToolInput):
    meter_id: str
    exclude_case_id: UUID | None = None
    limit: int = Field(default=10, ge=1, le=100)


class PrecedentCase(ToolOutput):
    case_id: UUID
    title: str
    status: str
    match_type: str
    outcome: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class PrecedentOutput(ToolOutput):
    status: str
    exact_meter_cases: list[PrecedentCase]
    similar_system_cases: list[PrecedentCase]
    pattern_summary: str


def find_meter_precedents(data: PrecedentInput, session: Session) -> PrecedentOutput:
    meter = MeterRepository(session).get(data.meter_id)
    if meter is None:
        raise ValueError("Meter not found")
    exact_rows = HistoryRepository(session).meter_cases(
        data.meter_id, limit=data.limit, exclude_case_id=data.exclude_case_id
    )
    exact = [
        PrecedentCase(
            case_id=row.id,
            title=row.title,
            status=row.status,
            match_type="exact_meter",
            outcome=row.status if row.status in {"closed", "resolved"} else None,
        )
        for row in exact_rows
    ]

    similar: list[PrecedentCase] = []
    if meter.customer_segment:
        statement = (
            select(models.Case)
            .join(models.CaseMeter)
            .join(models.Meter, models.Meter.id == models.CaseMeter.meter_id)
            .where(
                models.Meter.customer_segment == meter.customer_segment,
                models.Meter.id != meter.id,
                models.Case.status.in_(("closed", "resolved")),
            )
            .order_by(models.Case.updated_at.desc(), models.Case.id)
            .limit(data.limit)
        )
        excluded = [item.case_id for item in exact]
        if data.exclude_case_id:
            excluded.append(data.exclude_case_id)
        statement = statement.where(models.Case.id.not_in(excluded)).distinct()
        seen: set[UUID] = set()
        for row in session.scalars(statement):
            if row.id in seen:
                continue
            seen.add(row.id)
            similar.append(
                PrecedentCase(
                    case_id=row.id,
                    title=row.title,
                    status=row.status,
                    match_type="same_customer_segment",
                    outcome=row.status,
                )
            )
    status = "answered" if exact or similar else "unknown"
    summary = (
        f"Found {len(exact)} exact-meter and {len(similar)} similar closed cases."
        if status == "answered"
        else "No historical precedents were found."
    )
    return PrecedentOutput(
        status=status,
        exact_meter_cases=exact,
        similar_system_cases=similar,
        pattern_summary=summary,
        evidence=[
            EvidenceReference(
                source="database.case_history",
                kind="precedent_search",
                reliability=1,
                metadata={"exact_count": len(exact), "similar_count": len(similar)},
            )
        ],
        warnings=[] if status == "answered" else ["No precedents are available."],
    )
