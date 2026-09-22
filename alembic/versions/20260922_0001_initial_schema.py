"""Create the frozen Day 1 relational schema.

Revision ID: 20260922_0001
Revises:

This revision contains explicit DDL so later model changes cannot alter it.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy.vector
import sqlalchemy as sa

from alembic import op

revision: str = "20260922_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "assets",
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("asset_type", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("capacity", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["assets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assets_asset_type"), "assets", ["asset_type"], unique=False)
    op.create_index(op.f("ix_assets_parent_id"), "assets", ["parent_id"], unique=False)
    op.create_table(
        "cases",
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("triage_score", sa.Float(), nullable=True),
        sa.Column("priority_band", sa.String(length=2), nullable=True),
        sa.Column("active_rank", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("assigned_to", sa.String(length=120), nullable=True),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cases_priority_band"), "cases", ["priority_band"], unique=False)
    op.create_index(op.f("ix_cases_status"), "cases", ["status"], unique=False)
    op.create_table(
        "documents",
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("license", sa.String(length=120), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "events",
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_events_event_type"), "events", ["event_type"], unique=False)
    op.create_index(op.f("ix_events_status"), "events", ["status"], unique=False)
    op.create_table(
        "tariffs",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("customer_segment", sa.String(length=60), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("jod_per_kwh", sa.Float(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tariffs_customer_segment"), "tariffs", ["customer_segment"], unique=False
    )
    op.create_table(
        "actions",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_actions_case_id"), "actions", ["case_id"], unique=False)
    op.create_index(op.f("ix_actions_status"), "actions", ["status"], unique=False)
    op.create_table(
        "agent_runs",
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("trigger", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_runs_case_id"), "agent_runs", ["case_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_status"), "agent_runs", ["status"], unique=False)
    op.create_table(
        "case_events",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_case_events_case_id"), "case_events", ["case_id"], unique=False)
    op.create_index(op.f("ix_case_events_event_type"), "case_events", ["event_type"], unique=False)
    op.create_table(
        "document_chunks",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
        sa.Column("page_or_section", sa.String(length=120), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_document_chunks_document_id"), "document_chunks", ["document_id"], unique=False
    )
    op.create_table(
        "evidence",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("reliability", sa.Float(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_evidence_case_id"), "evidence", ["case_id"], unique=False)
    op.create_index(op.f("ix_evidence_kind"), "evidence", ["kind"], unique=False)
    op.create_table(
        "hypotheses",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("support_json", sa.JSON(), nullable=False),
        sa.Column("contradiction_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_hypotheses_case_id"), "hypotheses", ["case_id"], unique=False)
    op.create_table(
        "investigation_reports",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("answers_json", sa.JSON(), nullable=False),
        sa.Column("completeness", sa.Float(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "version", name="uq_report_case_version"),
    )
    op.create_index(
        op.f("ix_investigation_reports_case_id"), "investigation_reports", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_investigation_reports_status"), "investigation_reports", ["status"], unique=False
    )
    op.create_table(
        "meters",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("transformer_id", sa.Uuid(), nullable=True),
        sa.Column("location", sa.String(length=160), nullable=True),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("customer_segment", sa.String(length=60), nullable=True),
        sa.Column("has_solar", sa.Boolean(), nullable=True),
        sa.Column("has_ev", sa.Boolean(), nullable=True),
        sa.Column("metadata_source", sa.String(length=80), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["transformer_id"],
            ["assets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meters_status"), "meters", ["status"], unique=False)
    op.create_index(op.f("ix_meters_transformer_id"), "meters", ["transformer_id"], unique=False)
    op.create_table(
        "recommendations",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("risk", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_recommendations_case_id"), "recommendations", ["case_id"], unique=False
    )
    op.create_index(op.f("ix_recommendations_status"), "recommendations", ["status"], unique=False)
    op.create_table(
        "transformer_readings",
        sa.Column("transformer_id", sa.Uuid(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_kwh", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["transformer_id"],
            ["assets.id"],
        ),
        sa.PrimaryKeyConstraint("transformer_id", "timestamp"),
    )
    op.create_index(
        "ix_transformer_readings_time",
        "transformer_readings",
        ["transformer_id", "timestamp"],
        unique=False,
    )
    op.create_table(
        "anomalies",
        sa.Column("meter_id", sa.String(length=80), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=60), nullable=False),
        sa.Column("severity", sa.Float(), nullable=False),
        sa.Column("reliability_score", sa.Float(), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["meter_id"],
            ["meters.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_anomalies_detected_at"), "anomalies", ["detected_at"], unique=False)
    op.create_index(op.f("ix_anomalies_event_id"), "anomalies", ["event_id"], unique=False)
    op.create_index(op.f("ix_anomalies_meter_id"), "anomalies", ["meter_id"], unique=False)
    op.create_index(op.f("ix_anomalies_type"), "anomalies", ["type"], unique=False)
    op.create_table(
        "approvals",
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("decided_by", sa.String(length=120), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["recommendations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_approvals_recommendation_id"), "approvals", ["recommendation_id"], unique=False
    )
    op.create_table(
        "case_meters",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("meter_id", sa.String(length=80), nullable=False),
        sa.Column("relationship", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["meter_id"],
            ["meters.id"],
        ),
        sa.PrimaryKeyConstraint("case_id", "meter_id"),
    )
    op.create_table(
        "financial_impacts",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("missing_kwh_low", sa.Float(), nullable=False),
        sa.Column("missing_kwh_base", sa.Float(), nullable=False),
        sa.Column("missing_kwh_high", sa.Float(), nullable=False),
        sa.Column("risk_jod_low", sa.Float(), nullable=False),
        sa.Column("risk_jod_base", sa.Float(), nullable=False),
        sa.Column("risk_jod_high", sa.Float(), nullable=False),
        sa.Column("tariff_id", sa.Uuid(), nullable=False),
        sa.Column("assumptions_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["investigation_reports.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tariff_id"],
            ["tariffs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_financial_impacts_case_id"), "financial_impacts", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_financial_impacts_report_id"), "financial_impacts", ["report_id"], unique=False
    )
    op.create_table(
        "readings",
        sa.Column("meter_id", sa.String(length=80), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kwh", sa.Float(), nullable=False),
        sa.Column("quality_flag", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["meter_id"],
            ["meters.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meter_id", "timestamp", name="uq_reading_meter_time"),
    )
    op.create_index(op.f("ix_readings_meter_id"), "readings", ["meter_id"], unique=False)
    op.create_index(
        "ix_readings_meter_timestamp", "readings", ["meter_id", "timestamp"], unique=False
    )
    op.create_index(op.f("ix_readings_timestamp"), "readings", ["timestamp"], unique=False)
    op.create_table(
        "tool_executions",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tool_executions_run_id"), "tool_executions", ["run_id"], unique=False)
    op.create_index(op.f("ix_tool_executions_status"), "tool_executions", ["status"], unique=False)
    op.create_index(
        op.f("ix_tool_executions_tool_name"), "tool_executions", ["tool_name"], unique=False
    )
    op.create_table(
        "triage_assessments",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("band", sa.String(length=2), nullable=False),
        sa.Column("active_rank", sa.Integer(), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False),
        sa.Column("percentile", sa.Float(), nullable=False),
        sa.Column("factors_json", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["investigation_reports.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_triage_assessments_band"), "triage_assessments", ["band"], unique=False
    )
    op.create_index(
        op.f("ix_triage_assessments_case_id"), "triage_assessments", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_triage_assessments_report_id"), "triage_assessments", ["report_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_triage_assessments_report_id", table_name="triage_assessments")
    op.drop_index("ix_triage_assessments_case_id", table_name="triage_assessments")
    op.drop_index("ix_triage_assessments_band", table_name="triage_assessments")
    op.drop_index("ix_tool_executions_tool_name", table_name="tool_executions")
    op.drop_index("ix_tool_executions_status", table_name="tool_executions")
    op.drop_index("ix_tool_executions_run_id", table_name="tool_executions")
    op.drop_index("ix_readings_timestamp", table_name="readings")
    op.drop_index("ix_readings_meter_timestamp", table_name="readings")
    op.drop_index("ix_readings_meter_id", table_name="readings")
    op.drop_index("ix_financial_impacts_report_id", table_name="financial_impacts")
    op.drop_index("ix_financial_impacts_case_id", table_name="financial_impacts")
    op.drop_index("ix_approvals_recommendation_id", table_name="approvals")
    op.drop_index("ix_anomalies_type", table_name="anomalies")
    op.drop_index("ix_anomalies_meter_id", table_name="anomalies")
    op.drop_index("ix_anomalies_event_id", table_name="anomalies")
    op.drop_index("ix_anomalies_detected_at", table_name="anomalies")
    op.drop_index("ix_transformer_readings_time", table_name="transformer_readings")
    op.drop_index("ix_recommendations_status", table_name="recommendations")
    op.drop_index("ix_recommendations_case_id", table_name="recommendations")
    op.drop_index("ix_meters_transformer_id", table_name="meters")
    op.drop_index("ix_meters_status", table_name="meters")
    op.drop_index("ix_investigation_reports_status", table_name="investigation_reports")
    op.drop_index("ix_investigation_reports_case_id", table_name="investigation_reports")
    op.drop_index("ix_hypotheses_case_id", table_name="hypotheses")
    op.drop_index("ix_evidence_kind", table_name="evidence")
    op.drop_index("ix_evidence_case_id", table_name="evidence")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_index("ix_case_events_event_type", table_name="case_events")
    op.drop_index("ix_case_events_case_id", table_name="case_events")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_case_id", table_name="agent_runs")
    op.drop_index("ix_actions_status", table_name="actions")
    op.drop_index("ix_actions_case_id", table_name="actions")
    op.drop_index("ix_tariffs_customer_segment", table_name="tariffs")
    op.drop_index("ix_events_status", table_name="events")
    op.drop_index("ix_events_event_type", table_name="events")
    op.drop_index("ix_cases_status", table_name="cases")
    op.drop_index("ix_cases_priority_band", table_name="cases")
    op.drop_index("ix_assets_parent_id", table_name="assets")
    op.drop_index("ix_assets_asset_type", table_name="assets")
    op.drop_table("triage_assessments")
    op.drop_table("tool_executions")
    op.drop_table("readings")
    op.drop_table("financial_impacts")
    op.drop_table("case_meters")
    op.drop_table("approvals")
    op.drop_table("anomalies")
    op.drop_table("transformer_readings")
    op.drop_table("recommendations")
    op.drop_table("meters")
    op.drop_table("investigation_reports")
    op.drop_table("hypotheses")
    op.drop_table("evidence")
    op.drop_table("document_chunks")
    op.drop_table("case_events")
    op.drop_table("agent_runs")
    op.drop_table("actions")
    op.drop_table("tariffs")
    op.drop_table("events")
    op.drop_table("documents")
    op.drop_table("cases")
    op.drop_table("assets")
