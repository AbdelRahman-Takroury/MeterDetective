"""Deterministic, isolated reset and simulated repair for Day 6 Scenario 1."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import models
from app.services.investigation import (
    InvestigationService,
    ReplayCommand,
    ReplayResult,
    build_registry,
)
from app.tools.weather import WeatherClient

SCENARIO_ID = "scenario-1-local-drop"
METER_IDS = ("SC1-M1", "SC1-M2", "SC1-M3")
TARGET_METER = METER_IDS[0]
EVENT_TIME = datetime(2026, 9, 20, 12, tzinfo=UTC)
REPAIR_START = EVENT_TIME + timedelta(minutes=30)
REPAIR_END = REPAIR_START + timedelta(hours=2)
EVENT_KEY = f"{SCENARIO_ID}:replay"
TARIFF_NAME = "Scenario 1 synthetic residential tariff"
ASSET_IDS = (
    UUID("51000000-0000-4000-8000-000000000001"),
    UUID("51000000-0000-4000-8000-000000000002"),
    UUID("51000000-0000-4000-8000-000000000003"),
)


def _weather_payload(event_time: datetime = EVENT_TIME) -> dict[str, Any]:
    start = event_time - timedelta(hours=12)
    times = [(start + timedelta(hours=index)).isoformat() for index in range(25)]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [24 + (index % 5) * 0.2 for index in range(25)],
            "precipitation": [0 for _ in range(25)],
        }
    }


def scenario_registry(event_time: datetime = EVENT_TIME):
    """Use stable synthetic weather so demo output never depends on network availability."""
    client = WeatherClient(
        base_url="https://archive-api.open-meteo.com/v1/archive",
        retry_count=0,
        transport=lambda _url, _timeout: _weather_payload(event_time),
    )
    return build_registry(weather_client=client)


class ScenarioOneService:
    scenario_id = SCENARIO_ID
    meter_ids = METER_IDS
    target_meter = TARGET_METER
    event_time = EVENT_TIME
    repair_start = REPAIR_START
    repair_end = REPAIR_END
    event_key = EVENT_KEY
    tariff_name = TARIFF_NAME
    asset_ids = ASSET_IDS

    def __init__(self, session: Session) -> None:
        self.session = session

    def is_scenario_meter(self, meter_id: str) -> bool:
        return meter_id in self.meter_ids

    def is_scenario_case(self, case_id: UUID) -> bool:
        return bool(self.session.scalar(select(models.CaseMeter).where(
            models.CaseMeter.case_id == case_id,
            models.CaseMeter.meter_id == self.target_meter,
        )))

    def reset_and_run(self) -> tuple[ReplayResult, dict[str, int]]:
        removed = self._remove_previous_run()
        self._seed_anomaly()
        result = InvestigationService(self.session, scenario_registry()).replay(
            ReplayCommand(
                meter_id=self.target_meter,
                event_time=self.event_time,
                lookback_days=35,
                idempotency_key=self.event_key,
            )
        )
        if result.case_id is None or result.report_id is None:
            raise ValueError("Scenario 1 did not create an investigation case")
        self.session.add(models.CaseEvent(
            case_id=result.case_id,
            event_type="scenario_reset",
            details_json={
                "scenario_id": self.scenario_id,
                "meter_id": self.target_meter,
                "event_time": self.event_time.isoformat(),
                "drop_fraction": 0.70,
            },
        ))
        self.session.flush()
        return result, removed

    def apply_repair(self, action: models.Action) -> dict[str, Any] | None:
        if not self.is_scenario_case(action.case_id):
            return None
        if action.action_type not in {"technician_inspection", "meter_repair"}:
            raise ValueError("Scenario 1 repair requires a technician or meter repair action")
        existing = self.session.scalar(select(models.Reading.id).where(
            models.Reading.meter_id == self.target_meter,
            models.Reading.timestamp == self.repair_start,
            models.Reading.source == self.scenario_id,
        ))
        inserted = 0
        if existing is None:
            for index in range(4):
                timestamp = self.repair_start + timedelta(minutes=30 * index)
                shape = ((int(timestamp.timestamp() // 1800) % 12) - 6) * 0.05
                for meter_id, offset in zip(self.meter_ids, (0.0, 0.4, 0.8), strict=True):
                    self.session.add(models.Reading(
                        meter_id=meter_id,
                        timestamp=timestamp,
                        kwh=10 + offset + shape,
                        quality_flag="valid",
                        source=self.scenario_id,
                    ))
                    inserted += 1
                self.session.add(models.TransformerReading(
                    transformer_id=self.asset_ids[2],
                    timestamp=timestamp,
                    input_kwh=(31.2 + 3 * shape) * 1.03,
                ))
            self.session.add(models.CaseEvent(
                case_id=action.case_id,
                event_type="simulated_repair_applied",
                details_json={
                    "scenario_id": self.scenario_id,
                    "action_id": str(action.id),
                    "window_start": self.repair_start.isoformat(),
                    "window_end": self.repair_end.isoformat(),
                    "reading_count": inserted,
                },
            ))
        return {
            "simulation": True,
            "scenario_id": self.scenario_id,
            "repair": "applied",
            "readings_inserted": inserted,
            "verification_window": {
                "start": self.repair_start.isoformat(),
                "end": self.repair_end.isoformat(),
            },
        }

    def _seed_anomaly(self) -> None:
        self.session.add_all([
            models.Asset(id=self.asset_ids[0], asset_type="substation",
                         name="Scenario 1 substation",
                         metadata_json={"scenario_id": self.scenario_id}),
            models.Asset(id=self.asset_ids[1], parent_id=self.asset_ids[0], asset_type="feeder",
                         name="Scenario 1 feeder", metadata_json={"scenario_id": self.scenario_id}),
            models.Asset(id=self.asset_ids[2], parent_id=self.asset_ids[1],
                         asset_type="transformer",
                         name="Scenario 1 transformer", capacity=100,
                         metadata_json={"scenario_id": self.scenario_id}),
        ])
        for meter_id, label in zip(self.meter_ids, ("affected", "peer", "peer"), strict=True):
            self.session.add(models.Meter(
                id=meter_id,
                transformer_id=self.asset_ids[2],
                location="Amman synthetic Scenario 1 zone",
                type="smart",
                status="active",
                customer_segment="scenario_1_residential",
                has_solar=False,
                has_ev=False,
                metadata_source=self.scenario_id,
                metadata_json={"scenario_id": self.scenario_id, "role": label},
            ))
        start = self.event_time - timedelta(days=28)
        for index in range(int((self.event_time - start).total_seconds() / 1800)):
            timestamp = start + timedelta(minutes=30 * index)
            shape = ((index % 12) - 6) * 0.05
            for meter_id, offset in zip(self.meter_ids, (0.0, 0.4, 0.8), strict=True):
                self.session.add(models.Reading(
                    meter_id=meter_id,
                    timestamp=timestamp,
                    kwh=10 + offset + shape,
                    quality_flag="valid",
                    source=self.scenario_id,
                ))
        for meter_id, kwh in zip(self.meter_ids, (3.0, 10.4, 10.8), strict=True):
            self.session.add(models.Reading(
                meter_id=meter_id,
                timestamp=self.event_time,
                kwh=kwh,
                quality_flag="valid",
                source=self.scenario_id,
            ))
        self.session.add(models.TransformerReading(
            transformer_id=self.asset_ids[2], timestamp=self.event_time, input_kwh=24.2 * 1.03
        ))
        self.session.add(models.Tariff(
            name=self.tariff_name,
            customer_segment="scenario_1_residential",
            currency="JOD",
            jod_per_kwh=Decimal("0.120000"),
            effective_from=self.event_time - timedelta(days=365),
            source="Synthetic tariff for repeatable Scenario 1 demonstration",
            is_synthetic=True,
        ))
        self.session.flush()

    def _remove_previous_run(self) -> dict[str, int]:
        case_ids = list(self.session.scalars(select(models.CaseMeter.case_id).where(
            models.CaseMeter.meter_id.in_(self.meter_ids)
        )))
        report_ids = list(self.session.scalars(select(models.InvestigationReport.id).where(
            models.InvestigationReport.case_id.in_(case_ids)
        ))) if case_ids else []
        recommendation_ids = list(self.session.scalars(select(models.Recommendation.id).where(
            models.Recommendation.case_id.in_(case_ids)
        ))) if case_ids else []
        event_ids = list(self.session.scalars(select(models.Event.id).where(
            models.Event.payload_json["meter_id"].as_string().in_(self.meter_ids)
        )))
        run_ids = []
        for run in self.session.scalars(select(models.AgentRun)):
            if run.case_id in case_ids or run.trigger.get("event_id") in {
                str(item) for item in event_ids
            }:
                run_ids.append(run.id)

        targets = [
            ("tool_executions", models.ToolExecution, models.ToolExecution.run_id.in_(run_ids)),
            ("approvals", models.Approval,
             models.Approval.recommendation_id.in_(recommendation_ids)),
            ("actions", models.Action, models.Action.case_id.in_(case_ids)),
            ("recommendations", models.Recommendation,
             models.Recommendation.case_id.in_(case_ids)),
            ("financial_impacts", models.FinancialImpact,
             models.FinancialImpact.report_id.in_(report_ids)),
            ("triage_assessments", models.TriageAssessment,
             models.TriageAssessment.report_id.in_(report_ids)),
            ("case_events", models.CaseEvent, models.CaseEvent.case_id.in_(case_ids)),
            ("evidence", models.Evidence, models.Evidence.case_id.in_(case_ids)),
            ("hypotheses", models.Hypothesis, models.Hypothesis.case_id.in_(case_ids)),
            ("plans", models.InvestigationPlan, models.InvestigationPlan.case_id.in_(case_ids)),
            ("reports", models.InvestigationReport,
             models.InvestigationReport.case_id.in_(case_ids)),
            ("case_meters", models.CaseMeter, models.CaseMeter.case_id.in_(case_ids)),
            ("agent_runs", models.AgentRun, models.AgentRun.id.in_(run_ids)),
            ("cases", models.Case, models.Case.id.in_(case_ids)),
            ("anomalies", models.Anomaly, models.Anomaly.meter_id.in_(self.meter_ids)),
            ("events", models.Event, models.Event.id.in_(event_ids)),
            ("transformer_readings", models.TransformerReading,
             models.TransformerReading.transformer_id == self.asset_ids[2]),
            ("readings", models.Reading, models.Reading.meter_id.in_(self.meter_ids)),
            ("meters", models.Meter, models.Meter.id.in_(self.meter_ids)),
            ("tariffs", models.Tariff, models.Tariff.name == self.tariff_name),
        ]
        removed: dict[str, int] = {}
        for name, model, predicate in targets:
            result = self.session.execute(delete(model).where(predicate))
            removed[name] = result.rowcount or 0
        for asset_id in reversed(self.asset_ids):
            result = self.session.execute(delete(models.Asset).where(models.Asset.id == asset_id))
            removed["assets"] = removed.get("assets", 0) + (result.rowcount or 0)
        self.session.flush()
        return removed
