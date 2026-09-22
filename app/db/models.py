from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class IdMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Asset(IdMixin, Base):
    __tablename__ = "assets"
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("assets.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    capacity: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Meter(Base):
    __tablename__ = "meters"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    transformer_id: Mapped[UUID | None] = mapped_column(ForeignKey("assets.id"), index=True)
    location: Mapped[str | None] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), index=True)
    customer_segment: Mapped[str | None] = mapped_column(String(60))
    has_solar: Mapped[bool | None]
    has_ev: Mapped[bool | None]
    metadata_source: Mapped[str | None] = mapped_column(String(80))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Reading(IdMixin, Base):
    __tablename__ = "readings"
    meter_id: Mapped[str] = mapped_column(ForeignKey("meters.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    kwh: Mapped[float] = mapped_column(Float)
    quality_flag: Mapped[str] = mapped_column(String(40), default="valid")
    source: Mapped[str] = mapped_column(String(80))
    __table_args__ = (UniqueConstraint("meter_id", "timestamp", name="uq_reading_meter_time"),)


class TransformerReading(Base):
    __tablename__ = "transformer_readings"
    transformer_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    input_kwh: Mapped[float] = mapped_column(Float)


class Event(IdMixin, TimestampMixin, Base):
    __tablename__ = "events"
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), index=True)


class Anomaly(IdMixin, Base):
    __tablename__ = "anomalies"
    meter_id: Mapped[str] = mapped_column(ForeignKey("meters.id"), index=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    severity: Mapped[float] = mapped_column(Float)
    reliability_score: Mapped[float] = mapped_column(Float)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Case(IdMixin, Base):
    __tablename__ = "cases"
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), index=True)
    triage_score: Mapped[float | None] = mapped_column(Float)
    priority_band: Mapped[str | None] = mapped_column(String(2), index=True)
    active_rank: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    assigned_to: Mapped[str | None] = mapped_column(String(120))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CaseMeter(Base):
    __tablename__ = "case_meters"
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    meter_id: Mapped[str] = mapped_column(ForeignKey("meters.id"), primary_key=True)
    relationship: Mapped[str] = mapped_column(String(40))


class Evidence(IdMixin, TimestampMixin, Base):
    __tablename__ = "evidence"
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    source: Mapped[str] = mapped_column(String(120))
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    reliability: Mapped[float] = mapped_column(Float)


class Hypothesis(IdMixin, Base):
    __tablename__ = "hypotheses"
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    label: Mapped[str] = mapped_column(String(160))
    confidence: Mapped[float] = mapped_column(Float)
    support_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    contradiction_json: Mapped[list[Any]] = mapped_column(JSON, default=list)


class Recommendation(IdMixin, Base):
    __tablename__ = "recommendations"
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(80))
    rationale: Mapped[str] = mapped_column(Text)
    risk: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), index=True)


class Approval(IdMixin, Base):
    __tablename__ = "approvals"
    recommendation_id: Mapped[UUID] = mapped_column(ForeignKey("recommendations.id"), index=True)
    decision: Mapped[str] = mapped_column(String(30))
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)


class Action(IdMixin, Base):
    __tablename__ = "actions"
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), index=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(IdMixin, Base):
    __tablename__ = "agent_runs"
    case_id: Mapped[UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    trigger: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), index=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolExecution(IdMixin, Base):
    __tablename__ = "tool_executions"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), index=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Document(IdMixin, Base):
    __tablename__ = "documents"
    title: Mapped[str] = mapped_column(String(240))
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(120))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DocumentChunk(IdMixin, Base):
    __tablename__ = "document_chunks"
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    page_or_section: Mapped[str | None] = mapped_column(String(120))


class CaseEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "case_events"
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class InvestigationReport(IdMixin, Base):
    __tablename__ = "investigation_reports"
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True)
    answers_json: Mapped[list[Any]] = mapped_column(JSON)
    completeness: Mapped[float] = mapped_column(Float)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("case_id", "version", name="uq_report_case_version"),)


class Tariff(IdMixin, Base):
    __tablename__ = "tariffs"
    name: Mapped[str] = mapped_column(String(120))
    customer_segment: Mapped[str] = mapped_column(String(60), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="JOD")
    jod_per_kwh: Mapped[float] = mapped_column(Float)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(default=True)


class FinancialImpact(IdMixin, Base):
    __tablename__ = "financial_impacts"
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("investigation_reports.id"), index=True)
    missing_kwh_low: Mapped[float] = mapped_column(Float)
    missing_kwh_base: Mapped[float] = mapped_column(Float)
    missing_kwh_high: Mapped[float] = mapped_column(Float)
    risk_jod_low: Mapped[float] = mapped_column(Float)
    risk_jod_base: Mapped[float] = mapped_column(Float)
    risk_jod_high: Mapped[float] = mapped_column(Float)
    tariff_id: Mapped[UUID] = mapped_column(ForeignKey("tariffs.id"))
    assumptions_json: Mapped[list[Any]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)


class TriageAssessment(IdMixin, Base):
    __tablename__ = "triage_assessments"
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("investigation_reports.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    band: Mapped[str] = mapped_column(String(2), index=True)
    active_rank: Mapped[int] = mapped_column(Integer)
    active_count: Mapped[int] = mapped_column(Integer)
    percentile: Mapped[float] = mapped_column(Float)
    factors_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    policy_version: Mapped[str] = mapped_column(String(40))
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


Index("ix_readings_meter_timestamp", Reading.meter_id, Reading.timestamp)
Index(
    "ix_transformer_readings_time",
    TransformerReading.transformer_id,
    TransformerReading.timestamp,
)
