"""Transactional reading arrival and synchronous event dispatch.

No scenario labels are used to choose an outcome. The existing controller validates
readings and only advances past detection when it finds an anomaly.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.repositories import EventRepository, ReadingRepository
from app.services.investigation import (
    InvestigationFailure,
    InvestigationService,
    ReplayCommand,
    ReplayResult,
)
from app.tools.registry import ToolRegistry


class IncomingReading(BaseModel):
    meter_id: str = Field(min_length=1, max_length=80)
    timestamp: AwareDatetime
    kwh: float = Field(ge=0, allow_inf_nan=False)
    quality_flag: str = Field(default="valid", min_length=1, max_length=40)
    source: str = Field(min_length=1, max_length=80)
    model_config = ConfigDict(extra="forbid")


class ReadingEventService:
    def __init__(self, session: Session, registry: ToolRegistry | None = None) -> None:
        self.session = session
        self.controller = InvestigationService(session, registry)

    def receive(self, readings: list[IncomingReading]) -> list[ReplayResult]:
        """Store the entire arrival batch before dispatch so peers see a coherent window.

        Lock network assets in stable order on PostgreSQL to serialize matching events
        from the same transformer. Exact-meter locks also cover meters without topology.
        Canonical event keys make redelivery independent of caller-supplied identifiers.
        The caller owns commit/rollback; invalid batches leave no partial readings.
        """
        if not readings or len(readings) > 100:
            raise ValueError("Send between 1 and 100 readings at a time")
        ids = sorted({item.meter_id for item in readings})
        meters = list(self.session.scalars(select(models.Meter).where(
            models.Meter.id.in_(ids)
        ).order_by(models.Meter.id)))
        if len(meters) != len(ids):
            raise ValueError("One or more meters could not be found")
        transformers = {meter.transformer_id for meter in meters if meter.transformer_id}
        list(self.session.scalars(select(models.Asset).where(
            models.Asset.id.in_(transformers)
        ).order_by(models.Asset.id).with_for_update()))
        list(self.session.scalars(select(models.Meter).where(
            models.Meter.id.in_(ids)
        ).order_by(models.Meter.id).with_for_update()))
        events: dict[str, models.Event] = {}
        for item in readings:
            timestamp = item.timestamp.astimezone(UTC)
            reading = ReadingRepository(self.session).add(
                **item.model_dump(exclude={"timestamp"}), timestamp=timestamp
            )
            key = f"reading:{item.meter_id}:{timestamp.isoformat()}"
            event = EventRepository(self.session).add(
                idempotency_key=key,
                event_type="reading.received",
                payload={"meter_id": item.meter_id, "event_time": timestamp.isoformat()},
                status="pending",
            )
            # The reading's immutable identity and source remain in the readings table.
            assert reading.id is not None
            events[key] = event
        self.session.flush()
        results = []
        for key, event in sorted(events.items(), key=lambda entry: (
            entry[1].payload_json["event_time"], entry[1].payload_json["meter_id"]
        )):
            failed_traces: list[dict[str, Any]] = []
            failed_run_id = None
            failed_case_id = None
            try:
                # Failed processing must not discard the accepted reading or leave
                # a partially updated case. Keep each event atomic within the batch.
                with self.session.begin_nested():
                    try:
                        result = self.controller.replay(ReplayCommand(
                            meter_id=event.payload_json["meter_id"],
                            event_time=datetime.fromisoformat(event.payload_json["event_time"]),
                            idempotency_key=key,
                            resume_existing=True,
                        ))
                    except InvestigationFailure:
                        # Copy audit values before rolling back domain mutations.
                        failed_runs = [run for run in self.session.scalars(
                            select(models.AgentRun).where(models.AgentRun.status == "failed")
                        ) if run.trigger.get("event_id") == str(event.id)]
                        run_ids = [run.id for run in failed_runs]
                        if failed_runs:
                            failed_run_id = failed_runs[-1].id
                            failed_case_id = failed_runs[-1].case_id
                        for trace in self.session.scalars(select(models.ToolExecution).where(
                            models.ToolExecution.run_id.in_(run_ids)
                        ).order_by(models.ToolExecution.created_at)):
                            failed_traces.append({
                                field: getattr(trace, field) for field in (
                                    "tool_name", "input_json", "output_json", "status",
                                    "latency_ms", "error", "created_at",
                                )
                            })
                        raise
            except InvestigationFailure:
                event.status = "failed"
                # A newly created case was part of the rolled-back savepoint and
                # must not remain as a dangling foreign key in the audit run.
                if (
                    failed_case_id is not None
                    and self.session.get(models.Case, failed_case_id) is None
                ):
                    failed_case_id = None
                run = models.AgentRun(
                    id=failed_run_id,
                    case_id=failed_case_id,
                    trigger={"event_id": str(event.id), **event.payload_json},
                    status="failed", ended_at=datetime.now(UTC),
                    state_json={
                        "stage": "failed",
                        "message": "Investigation could not complete. The readings are saved.",
                        "domain_changes_rolled_back": True,
                    },
                )
                self.session.add(run)
                self.session.flush()
                for trace in failed_traces:
                    # Keep original inputs verbatim for audit; rolled-back IDs are
                    # observations, not committed domain records.
                    self.session.add(models.ToolExecution(run_id=run.id, **trace))
                self.session.flush()
                result = ReplayResult(
                    event_id=event.id, run_id=run.id, status="failed",
                    case_id=failed_case_id,
                    tool_calls=len(failed_traces),
                )
            results.append(result)
        return results
