"""Add Day 2-B integrity rules and investigation lookup indexes.

Revision ID: 20260922_0002
Revises: 20260922_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260922_0002"
down_revision: str | None = "20260922_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing Day 1 events receive stable keys before the column becomes required.
    op.add_column("events", sa.Column("idempotency_key", sa.String(160), nullable=True))
    op.execute("UPDATE events SET idempotency_key = 'legacy:' || id::text")
    op.alter_column("events", "idempotency_key", existing_type=sa.String(160), nullable=False)
    op.create_unique_constraint(op.f("uq_event_idempotency_key"), "events", ["idempotency_key"])
    op.create_index("ix_events_created_at", "events", ["created_at"])

    op.create_check_constraint(
        op.f("ck_assets_asset_capacity_nonnegative"),
        "assets",
        "capacity IS NULL OR capacity >= 0",
    )
    op.create_index("ix_transformer_readings_timestamp", "transformer_readings", ["timestamp"])
    op.create_index(
        "ix_transformer_readings_transformer_timestamp",
        "transformer_readings",
        ["transformer_id", "timestamp"],
    )
    op.drop_index("ix_transformer_readings_time", table_name="transformer_readings")

    op.create_check_constraint(
        op.f("ck_anomalies_anomaly_reliability_range"),
        "anomalies",
        "reliability_score >= 0 AND reliability_score <= 1",
    )
    op.create_unique_constraint(
        op.f("uq_anomaly_event_meter_type"), "anomalies", ["event_id", "meter_id", "type"]
    )
    op.create_check_constraint(
        op.f("ck_cases_case_triage_score_range"),
        "cases",
        "triage_score IS NULL OR (triage_score >= 0 AND triage_score <= 100)",
    )
    op.create_check_constraint(
        op.f("ck_cases_case_confidence_range"),
        "cases",
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
    )
    op.create_check_constraint(
        op.f("ck_cases_case_active_rank_positive"),
        "cases",
        "active_rank IS NULL OR active_rank >= 1",
    )
    op.create_check_constraint(
        op.f("ck_cases_case_priority_band_values"),
        "cases",
        "priority_band IS NULL OR priority_band IN ('P1', 'P2', 'P3', 'P4')",
    )
    op.create_index("ix_cases_status_priority", "cases", ["status", "priority_band"])

    op.create_check_constraint(
        op.f("ck_evidence_evidence_reliability_range"),
        "evidence",
        "reliability >= 0 AND reliability <= 1",
    )
    op.create_check_constraint(
        op.f("ck_hypotheses_hypothesis_confidence_range"),
        "hypotheses",
        "confidence >= 0 AND confidence <= 1",
    )
    op.create_check_constraint(
        op.f("ck_investigation_reports_report_version_positive"),
        "investigation_reports",
        "version >= 1",
    )
    op.create_check_constraint(
        op.f("ck_investigation_reports_report_completeness_range"),
        "investigation_reports",
        "completeness >= 0 AND completeness <= 1",
    )
    op.create_index(
        "ix_investigation_reports_case_generated",
        "investigation_reports",
        ["case_id", "generated_at"],
    )

    op.alter_column(
        "tariffs",
        "jod_per_kwh",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 6),
        postgresql_using="jod_per_kwh::numeric(12, 6)",
    )
    op.create_unique_constraint(
        op.f("uq_tariff_identity"),
        "tariffs",
        ["name", "customer_segment", "effective_from"],
    )
    op.create_check_constraint(
        op.f("ck_tariffs_tariff_rate_nonnegative"), "tariffs", "jod_per_kwh >= 0"
    )
    op.create_check_constraint(
        op.f("ck_tariffs_tariff_effective_window"),
        "tariffs",
        "effective_to IS NULL OR effective_to > effective_from",
    )
    op.create_index(
        "ix_tariffs_segment_effective_window",
        "tariffs",
        ["customer_segment", "effective_from", "effective_to"],
    )

    for column in ("risk_jod_low", "risk_jod_base", "risk_jod_high"):
        op.alter_column(
            "financial_impacts",
            column,
            existing_type=sa.Float(),
            type_=sa.Numeric(16, 3),
            postgresql_using=f"{column}::numeric(16, 3)",
        )
    op.create_unique_constraint(
        op.f("uq_financial_impact_report"), "financial_impacts", ["report_id"]
    )
    op.create_check_constraint(
        op.f("ck_financial_impacts_financial_missing_kwh_order"),
        "financial_impacts",
        "missing_kwh_low >= 0 AND missing_kwh_low <= missing_kwh_base "
        "AND missing_kwh_base <= missing_kwh_high",
    )
    op.create_check_constraint(
        op.f("ck_financial_impacts_financial_risk_jod_order"),
        "financial_impacts",
        "risk_jod_low >= 0 AND risk_jod_low <= risk_jod_base AND risk_jod_base <= risk_jod_high",
    )
    op.create_check_constraint(
        op.f("ck_financial_impacts_financial_confidence_range"),
        "financial_impacts",
        "confidence >= 0 AND confidence <= 1",
    )

    op.create_unique_constraint(
        op.f("uq_triage_assessment_report"), "triage_assessments", ["report_id"]
    )
    for name, condition in (
        ("triage_score_range", "score >= 0 AND score <= 100"),
        ("triage_active_rank_positive", "active_rank >= 1"),
        ("triage_active_count_positive", "active_count >= 1"),
        ("triage_rank_within_count", "active_rank <= active_count"),
        ("triage_band_values", "band IN ('P1', 'P2', 'P3', 'P4')"),
        ("triage_percentile_range", "percentile >= 0 AND percentile <= 100"),
    ):
        op.create_check_constraint(
            op.f(f"ck_triage_assessments_{name}"), "triage_assessments", condition
        )
    op.create_index("ix_triage_assessments_rank", "triage_assessments", ["band", "active_rank"])
    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding_hnsw", table_name="document_chunks")
    op.drop_index("ix_triage_assessments_rank", table_name="triage_assessments")
    for name in (
        "triage_percentile_range",
        "triage_band_values",
        "triage_rank_within_count",
        "triage_active_count_positive",
        "triage_active_rank_positive",
        "triage_score_range",
    ):
        op.drop_constraint(
            op.f(f"ck_triage_assessments_{name}"), "triage_assessments", type_="check"
        )
    op.drop_constraint(op.f("uq_triage_assessment_report"), "triage_assessments", type_="unique")

    for name in (
        "financial_confidence_range",
        "financial_risk_jod_order",
        "financial_missing_kwh_order",
    ):
        op.drop_constraint(op.f(f"ck_financial_impacts_{name}"), "financial_impacts", type_="check")
    op.drop_constraint(op.f("uq_financial_impact_report"), "financial_impacts", type_="unique")
    for column in ("risk_jod_low", "risk_jod_base", "risk_jod_high"):
        op.alter_column(
            "financial_impacts",
            column,
            existing_type=sa.Numeric(16, 3),
            type_=sa.Float(),
            postgresql_using=f"{column}::double precision",
        )

    op.drop_index("ix_tariffs_segment_effective_window", table_name="tariffs")
    op.drop_constraint(op.f("ck_tariffs_tariff_effective_window"), "tariffs", type_="check")
    op.drop_constraint(op.f("ck_tariffs_tariff_rate_nonnegative"), "tariffs", type_="check")
    op.drop_constraint(op.f("uq_tariff_identity"), "tariffs", type_="unique")
    op.alter_column(
        "tariffs",
        "jod_per_kwh",
        existing_type=sa.Numeric(12, 6),
        type_=sa.Float(),
        postgresql_using="jod_per_kwh::double precision",
    )

    op.drop_index("ix_investigation_reports_case_generated", table_name="investigation_reports")
    op.drop_constraint(
        op.f("ck_investigation_reports_report_completeness_range"),
        "investigation_reports",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_investigation_reports_report_version_positive"),
        "investigation_reports",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_hypotheses_hypothesis_confidence_range"), "hypotheses", type_="check"
    )
    op.drop_constraint(op.f("ck_evidence_evidence_reliability_range"), "evidence", type_="check")

    op.drop_index("ix_cases_status_priority", table_name="cases")
    for name in (
        "case_priority_band_values",
        "case_active_rank_positive",
        "case_confidence_range",
        "case_triage_score_range",
    ):
        op.drop_constraint(op.f(f"ck_cases_{name}"), "cases", type_="check")
    op.drop_constraint(op.f("uq_anomaly_event_meter_type"), "anomalies", type_="unique")
    op.drop_constraint(op.f("ck_anomalies_anomaly_reliability_range"), "anomalies", type_="check")

    op.create_index(
        "ix_transformer_readings_time",
        "transformer_readings",
        ["transformer_id", "timestamp"],
    )
    op.drop_index(
        "ix_transformer_readings_transformer_timestamp", table_name="transformer_readings"
    )
    op.drop_index("ix_transformer_readings_timestamp", table_name="transformer_readings")
    op.drop_constraint(op.f("ck_assets_asset_capacity_nonnegative"), "assets", type_="check")

    op.drop_index("ix_events_created_at", table_name="events")
    op.drop_constraint(op.f("uq_event_idempotency_key"), "events", type_="unique")
    op.drop_column("events", "idempotency_key")
