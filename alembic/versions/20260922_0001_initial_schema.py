"""Create the frozen Day 1 relational schema.

Revision ID: 20260922_0001
Revises:
"""

from collections.abc import Sequence

from alembic import op
from app.db import models  # noqa: F401
from app.db.base import Base

revision: str = "20260922_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
