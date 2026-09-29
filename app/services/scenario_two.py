"""Isolated Scenario 2 fixture, replayed through the normal reading-event pipeline."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select

from app.db import models
from app.services.investigation import ReplayResult
from app.services.reading_events import IncomingReading, ReadingEventService
from app.services.scenario_one import ScenarioOneService, scenario_registry

SCENARIO_ID = "scenario-2-shared-drop"
EVENT_TIME = datetime(2026, 9, 21, 12, tzinfo=UTC)
METER_IDS = ("SC2-M1", "SC2-M2", "SC2-M3")
ASSET_IDS = tuple(UUID(f"52000000-0000-4000-8000-{index:012d}") for index in (1, 2, 3))
Stage = Literal["normal", "initial", "shared"]


class ScenarioTwoService(ScenarioOneService):
    event_time = EVENT_TIME
    scenario_id = SCENARIO_ID
    meter_ids = METER_IDS
    target_meter = METER_IDS[0]
    asset_ids = ASSET_IDS
    event_key = f"{SCENARIO_ID}:replay"
    tariff_name = "Scenario 2 synthetic residential tariff"

    def reset(self) -> dict[str, int]:
        removed = self._remove_previous_run()
        self._seed_anomaly()
        # Reset loads history only. Live readings and their events arrive via replay.
        self.session.execute(delete(models.Reading).where(
            models.Reading.meter_id.in_(self.meter_ids),
            models.Reading.timestamp == EVENT_TIME,
        ))
        self.session.execute(delete(models.TransformerReading).where(
            models.TransformerReading.transformer_id == self.asset_ids[2],
        ))
        for index, asset_id in enumerate(self.asset_ids):
            self.session.get(models.Asset, asset_id).name = (
                "Scenario 2 " + ("substation", "feeder", "transformer")[index]
            )
        for meter_id in self.meter_ids:
            meter = self.session.get(models.Meter, meter_id)
            meter.customer_segment = "scenario_2_residential"
            meter.location = "Amman synthetic Scenario 2 zone"
        tariff = self.session.scalar(select(models.Tariff).where(
            models.Tariff.name == self.tariff_name
        ))
        tariff.customer_segment = "scenario_2_residential"
        tariff.source = "Synthetic tariff for repeatable Scenario 2 demonstration"
        self.session.flush()
        return removed

    def replay_stage(self, stage: Stage) -> list[ReplayResult]:
        if self.session.get(models.Meter, self.target_meter) is None:
            raise ValueError("Prepare Scenario 2 with reset before playing readings")
        if stage == "shared":
            first_key = f"reading:{self.target_meter}:{EVENT_TIME.isoformat()}"
            first_event = self.session.scalar(select(models.Event).where(
                models.Event.idempotency_key == first_key,
                models.Event.status == "completed",
            ))
            if first_event is None:
                raise ValueError("Play the initial readings before the follow-up readings")
        timestamp = EVENT_TIME + timedelta(minutes=30 if stage == "shared" else 0)
        if stage == "normal":
            timestamp = EVENT_TIME - timedelta(minutes=30)
            values = (10.25, 10.65, 11.05)
        else:
            values = (3.0, 3.12, 3.24) if stage == "shared" else (3.0, 10.4, 10.8)
        # Later transformer evidence is stored before readings are dispatched.
        # Stage A deliberately has no transformer measurement.
        if stage == "shared" and self.session.get(
            models.TransformerReading, (self.asset_ids[2], timestamp)
        ) is None:
            self.session.add(models.TransformerReading(
                transformer_id=self.asset_ids[2], timestamp=timestamp, input_kwh=32.136,
            ))
            self.session.flush()
        return ReadingEventService(self.session, scenario_registry(EVENT_TIME)).receive([
            IncomingReading(
                meter_id=meter_id, timestamp=timestamp, kwh=kwh,
                source=self.scenario_id,
            )
            for meter_id, kwh in zip(self.meter_ids, values, strict=True)
        ])
