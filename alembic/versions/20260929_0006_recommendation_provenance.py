"""Link recommendations to plans/reports and preserve supersession lineage.

Revision ID: 20260929_0006
Revises: 20260928_0005
"""

import sqlalchemy as sa

from alembic import op

revision = "20260929_0006"
down_revision = "20260928_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("plan_id", sa.Uuid(), nullable=True))
    op.add_column(
        "recommendations", sa.Column("source_report_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "recommendations", sa.Column("superseded_by_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "recommendations", sa.Column("superseded_at", sa.DateTime(timezone=True))
    )
    op.create_foreign_key(
        "fk_recommendations_plan_id_investigation_plans",
        "recommendations", "investigation_plans", ["plan_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_recommendations_source_report_id_investigation_reports",
        "recommendations", "investigation_reports", ["source_report_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_recommendations_superseded_by_id_recommendations",
        "recommendations", "recommendations", ["superseded_by_id"], ["id"],
    )
    op.create_index("ix_recommendations_plan_id", "recommendations", ["plan_id"])
    op.create_index(
        "ix_recommendations_source_report_id", "recommendations", ["source_report_id"]
    )
    op.create_index(
        "ix_recommendations_superseded_by_id", "recommendations", ["superseded_by_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_recommendations_superseded_by_id", table_name="recommendations")
    op.drop_index("ix_recommendations_source_report_id", table_name="recommendations")
    op.drop_index("ix_recommendations_plan_id", table_name="recommendations")
    op.drop_constraint(
        "fk_recommendations_superseded_by_id_recommendations",
        "recommendations", type_="foreignkey",
    )
    op.drop_constraint(
        "fk_recommendations_source_report_id_investigation_reports",
        "recommendations", type_="foreignkey",
    )
    op.drop_constraint(
        "fk_recommendations_plan_id_investigation_plans",
        "recommendations", type_="foreignkey",
    )
    op.drop_column("recommendations", "superseded_at")
    op.drop_column("recommendations", "superseded_by_id")
    op.drop_column("recommendations", "source_report_id")
    op.drop_column("recommendations", "plan_id")
