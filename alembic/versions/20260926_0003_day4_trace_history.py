"""Add ordered tool traces and hypothesis confidence history.

Revision ID: 20260926_0003
Revises: 20260922_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260926_0003"
down_revision: str | None = "20260922_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hypotheses",
        sa.Column("update_history_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "hypotheses",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "tool_executions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_tool_executions_run_created", "tool_executions", ["run_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_tool_executions_run_created", table_name="tool_executions")
    op.drop_column("tool_executions", "created_at")
    op.drop_column("hypotheses", "updated_at")
    op.drop_column("hypotheses", "update_history_json")
