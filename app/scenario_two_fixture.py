"""Pure, deterministic input evidence for Day 7-A Scenario 2.

This module deliberately supplies observations and metadata only. Existing
analytics decide what those inputs mean; no result labels or calculations live
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID

from app.tools.analytics import AssetNode, MeterConnection, MeterSeries, ReadingPoint

SCENARIO_ID: Final = "scenario-2-evidence-replan"
METER_IDS: Final = ("SC2-M1", "SC2-M2", "SC2-M3")
TARGET_METER: Final = METER_IDS[0]
RELATED_METERS: Final = METER_IDS[1:]
CUSTOMER_SEGMENT: Final = "scenario_2_residential"

STAGE_A_TIME: Final = datetime(2026, 9, 21, 12, tzinfo=UTC)
STAGE_B_TIME: Final = datetime(2026, 9, 21, 12, 30, tzinfo=UTC)
HISTORICAL_START: Final = STAGE_A_TIME - timedelta(days=28)
CADENCE: Final = timedelta(minutes=30)

SUBSTATION_ID: Final = UUID("52000000-0000-4000-8000-000000000001")
FEEDER_ID: Final = UUID("52000000-0000-4000-8000-000000000002")
TRANSFORMER_ID: Final = UUID("52000000-0000-4000-8000-000000000003")
ASSET_IDS: Final = (SUBSTATION_ID, FEEDER_ID, TRANSFORMER_ID)

TARIFF_NAME: Final = "Scenario 2 synthetic residential tariff"
TARIFF_SOURCE: Final = "Synthetic tariff for repeatable Scenario 2 demonstration"
TARIFF_JOD_PER_KWH: Final = Decimal("0.120000")


@dataclass(frozen=True)
class MeterMetadata:
    """Minimum deterministic metadata needed by peer selection and persistence."""

    meter_id: str
    transformer_id: UUID
    meter_type: str
    status: str
    customer_segment: str
    has_solar: bool
    has_ev: bool
    metadata_source: str


@dataclass(frozen=True)
class TransformerReadingFixture:
    """A transformer observation supplied as fixture input, without interpretation."""

    transformer_id: UUID
    timestamp: datetime
    input_kwh: float


@dataclass(frozen=True)
class TariffFixture:
    """Caller-resolved synthetic tariff input for revenue analytics."""

    name: str
    customer_segment: str
    currency: str
    jod_per_kwh: Decimal
    effective_from: datetime
    source: str
    is_synthetic: bool


METER_METADATA: Final = tuple(
    MeterMetadata(
        meter_id=meter_id,
        transformer_id=TRANSFORMER_ID,
        meter_type="smart",
        status="active",
        customer_segment=CUSTOMER_SEGMENT,
        has_solar=False,
        has_ev=False,
        metadata_source=SCENARIO_ID,
    )
    for meter_id in METER_IDS
)

TOPOLOGY_ASSETS: Final = (
    AssetNode(asset_id=str(SUBSTATION_ID), asset_type="substation"),
    AssetNode(asset_id=str(FEEDER_ID), asset_type="feeder", parent_id=str(SUBSTATION_ID)),
    AssetNode(
        asset_id=str(TRANSFORMER_ID),
        asset_type="transformer",
        parent_id=str(FEEDER_ID),
    ),
)
TOPOLOGY_METER_CONNECTIONS: Final = tuple(
    MeterConnection(meter_id=meter_id, transformer_id=str(TRANSFORMER_ID))
    for meter_id in METER_IDS
)

TARIFF: Final = TariffFixture(
    name=TARIFF_NAME,
    customer_segment=CUSTOMER_SEGMENT,
    currency="JOD",
    jod_per_kwh=TARIFF_JOD_PER_KWH,
    effective_from=STAGE_A_TIME - timedelta(days=365),
    source=TARIFF_SOURCE,
    is_synthetic=True,
)


def historical_readings() -> dict[str, tuple[ReadingPoint, ...]]:
    """Return fresh, ordered readings strictly before the first stage."""

    interval_count = int((STAGE_A_TIME - HISTORICAL_START) / CADENCE)
    readings: dict[str, list[ReadingPoint]] = {meter_id: [] for meter_id in METER_IDS}
    for index in range(interval_count):
        timestamp = HISTORICAL_START + CADENCE * index
        shape = ((index % 12) - 6) * 0.05
        for meter_id, base_kwh in zip(METER_IDS, (10.0, 10.4, 10.8), strict=True):
            readings[meter_id].append(
                ReadingPoint(timestamp=timestamp, kwh=base_kwh + shape, quality_flag="valid")
            )
    return {meter_id: tuple(points) for meter_id, points in readings.items()}


def stage_a_meter_readings() -> dict[str, tuple[ReadingPoint, ...]]:
    """Return only the observations available at the first stage."""

    return _event_readings(STAGE_A_TIME, (3.0, 10.4, 10.8))


def stage_b_meter_readings() -> dict[str, tuple[ReadingPoint, ...]]:
    """Return only the later observations added at the second stage."""

    return _event_readings(STAGE_B_TIME, (3.0, 3.0, 3.0))


def stage_a_transformer_reading() -> TransformerReadingFixture:
    """Return the transformer observation available at the first stage."""

    return TransformerReadingFixture(
        transformer_id=TRANSFORMER_ID,
        timestamp=STAGE_A_TIME,
        input_kwh=24.926,
    )


def stage_b_transformer_reading() -> TransformerReadingFixture:
    """Return the transformer observation added at the second stage."""

    return TransformerReadingFixture(
        transformer_id=TRANSFORMER_ID,
        timestamp=STAGE_B_TIME,
        input_kwh=24.926,
    )


def meter_series(
    readings_by_meter: dict[str, tuple[ReadingPoint, ...]],
) -> tuple[MeterSeries, ...]:
    """Build analytics-ready series from an explicitly supplied evidence collection."""

    metadata_by_meter = {item.meter_id: item for item in METER_METADATA}
    return tuple(
        MeterSeries(
            meter_id=meter_id,
            customer_segment=metadata_by_meter[meter_id].customer_segment,
            has_solar=metadata_by_meter[meter_id].has_solar,
            has_ev=metadata_by_meter[meter_id].has_ev,
            readings=list(readings_by_meter[meter_id]),
        )
        for meter_id in METER_IDS
    )


def _event_readings(
    timestamp: datetime,
    values: tuple[float, float, float],
) -> dict[str, tuple[ReadingPoint, ...]]:
    return {
        meter_id: (ReadingPoint(timestamp=timestamp, kwh=kwh, quality_flag="valid"),)
        for meter_id, kwh in zip(METER_IDS, values, strict=True)
    }
