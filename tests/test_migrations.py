from io import StringIO

from alembic.config import Config

from alembic import command
from app.db.base import Base


def _migration_sql(target: str, *, downgrade: bool = False) -> str:
    buffer = StringIO()
    config = Config("alembic.ini", output_buffer=buffer)
    if downgrade:
        command.downgrade(config, target, sql=True)
    else:
        command.upgrade(config, target, sql=True)
    return buffer.getvalue()


def test_offline_upgrade_covers_current_schema() -> None:
    sql = _migration_sql("head")

    for table in Base.metadata.tables.values():
        assert f"CREATE TABLE {table.name}" in sql
        for index in table.indexes:
            assert index.name in sql
        for constraint in table.constraints:
            if constraint.name:
                assert constraint.name in sql

    assert "UPDATE events SET idempotency_key = 'legacy:' || id::text" in sql
    assert "ALTER TABLE events ALTER COLUMN idempotency_key SET NOT NULL" in sql
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "UPDATE alembic_version SET version_num='20260922_0002'" in sql


def test_offline_downgrade_removes_day2b_then_baseline_schema() -> None:
    sql = _migration_sql("head:base", downgrade=True)

    assert "DROP INDEX ix_document_chunks_embedding_hnsw" in sql
    assert "ALTER TABLE events DROP COLUMN idempotency_key" in sql
    for table_name in Base.metadata.tables:
        assert f"DROP TABLE {table_name}" in sql
