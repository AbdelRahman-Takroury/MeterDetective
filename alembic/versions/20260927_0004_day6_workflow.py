"""Complete the recommendation, approval, and action workflow schema.

Revision ID: 20260927_0004
Revises: 20260926_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260927_0004"
down_revision: str | None = "20260926_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recommendations",
        sa.Column(
            "requires_approval", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "recommendations",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "recommendations",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_unique_constraint(
        "uq_approval_recommendation", "approvals", ["recommendation_id"]
    )
    op.create_check_constraint(
        "ck_approvals_approval_decision_values",
        "approvals",
        "decision IN ('approved', 'rejected')",
    )

    op.add_column(
        "actions", sa.Column("recommendation_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "actions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_foreign_key(
        "fk_actions_recommendation_id_recommendations",
        "actions",
        "recommendations",
        ["recommendation_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_actions_recommendation_id", "actions", ["recommendation_id"]
    )
    op.create_index(
        "ix_actions_recommendation_id", "actions", ["recommendation_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_actions_recommendation_id", table_name="actions")
    op.drop_constraint("uq_actions_recommendation_id", "actions", type_="unique")
    op.drop_constraint(
        "fk_actions_recommendation_id_recommendations", "actions", type_="foreignkey"
    )
    op.drop_column("actions", "created_at")
    op.drop_column("actions", "recommendation_id")

    op.drop_constraint(
        "ck_approvals_approval_decision_values", "approvals", type_="check"
    )
    op.drop_constraint("uq_approval_recommendation", "approvals", type_="unique")

    op.drop_column("recommendations", "updated_at")
    op.drop_column("recommendations", "created_at")
    op.drop_column("recommendations", "requires_approval")
