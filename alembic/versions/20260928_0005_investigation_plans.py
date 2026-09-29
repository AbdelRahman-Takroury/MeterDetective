"""Persist evidence-linked investigation plan versions.

Revision ID: 20260928_0005
Revises: 20260927_0004
"""

import sqlalchemy as sa

from alembic import op

revision = "20260928_0005"
down_revision = "20260927_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigation_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("source_report_id", sa.Uuid(),
                  sa.ForeignKey("investigation_reports.id"), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("case_id", "version", name="uq_plan_case_version"),
        sa.CheckConstraint("version >= 1", name="plan_version_positive"),
        sa.CheckConstraint(
            "status IN ('active', 'invalidated', 'superseded')", name="plan_status_values"
        ),
        sa.CheckConstraint("scope IN ('local', 'shared', 'unknown')", name="plan_scope_values"),
    )
    op.create_index("ix_investigation_plans_case_id", "investigation_plans", ["case_id"])
    op.create_index("uq_plan_active_case", "investigation_plans", ["case_id"], unique=True,
                    postgresql_where=sa.text("status = 'active'"),
                    sqlite_where=sa.text("status = 'active'"))


def downgrade() -> None:
    op.drop_index("uq_plan_active_case", table_name="investigation_plans")
    op.drop_index("ix_investigation_plans_case_id", table_name="investigation_plans")
    op.drop_table("investigation_plans")
