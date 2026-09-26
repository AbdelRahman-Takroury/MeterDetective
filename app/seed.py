"""Transactional, repeatable import for prepared Day 2 data."""

import argparse
import csv
import json
import math
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Uuid,
    insert,
    select,
)
from sqlalchemy import tuple_ as sql_tuple
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.db import models  # noqa: F401 - registers tables on Base.metadata
from app.db.base import Base
from app.db.session import engine

DEFAULT_MANIFEST = Path("data/seed/day2b.json")
BATCH_SIZE = 200

# The order satisfies foreign keys. Asset rows themselves must be parent-first.
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "assets": ("id",),
    "meters": ("id",),
    "readings": ("meter_id", "timestamp"),
    "transformer_readings": ("transformer_id", "timestamp"),
    "events": ("idempotency_key",),
    "anomalies": ("event_id", "meter_id", "type"),
    "cases": ("id",),
    "case_meters": ("case_id", "meter_id"),
    "investigation_reports": ("case_id", "version"),
    "tariffs": ("name", "customer_segment", "effective_from"),
    "financial_impacts": ("report_id",),
    "triage_assessments": ("report_id",),
}


class SeedError(ValueError):
    """Invalid manifest or conflicting seed data."""


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SeedError(f"Cannot read manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        raise SeedError("Manifest must be an object with format_version: 1")
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise SeedError("Manifest tables must be a nonempty object")
    unknown = set(tables) - TABLE_KEYS.keys()
    if unknown:
        raise SeedError(f"Unsupported seed tables: {', '.join(sorted(unknown))}")
    return tables


def _rows(specification: Any, directory: Path) -> Iterator[tuple[int, Mapping[str, Any], bool]]:
    if isinstance(specification, list):
        source, is_csv = specification, False
    elif isinstance(specification, dict) and set(specification) == {"file"}:
        if not isinstance(specification["file"], str):
            raise SeedError("file reference must be a string")
        path = directory / specification["file"]
        if path.suffix.lower() == ".csv":
            try:
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    for number, row in enumerate(csv.DictReader(stream), start=2):
                        yield number, row, True
            except OSError as exc:
                raise SeedError(f"Cannot read seed file {path}: {exc}") from exc
            return
        if path.suffix.lower() != ".json":
            raise SeedError(f"Seed file must be CSV or JSON: {path}")
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SeedError(f"Cannot read seed file {path}: {exc}") from exc
        is_csv = False
    else:
        raise SeedError("A table must contain a row list or a {'file': 'name.csv'} reference")
    if not isinstance(source, list):
        raise SeedError("JSON seed files must contain a row list")
    for number, row in enumerate(source, start=1):
        yield number, row, is_csv


def _parse_value(value: Any, column: Any, *, is_csv: bool) -> Any:
    if value is None or (is_csv and value == ""):
        return None
    type_ = column.type
    if isinstance(type_, Uuid):
        return UUID(str(value))
    if isinstance(type_, DateTime):
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None:
            raise SeedError("timestamps must include a UTC offset")
        return parsed.astimezone(UTC)
    if isinstance(type_, Boolean):
        if isinstance(value, bool):
            return value
        if is_csv and isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise SeedError("boolean fields must be true or false")
    if isinstance(type_, Integer):
        if isinstance(value, bool):
            raise SeedError("integer fields cannot be boolean")
        return int(value)
    if isinstance(type_, Float):
        if isinstance(value, bool):
            raise SeedError("numeric fields cannot be boolean")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise SeedError("numeric fields must be finite")
        return parsed
    if isinstance(type_, Numeric):
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise SeedError("numeric fields must be finite")
        return parsed
    if isinstance(type_, JSON):
        parsed = json.loads(value) if is_csv else value
        json.dumps(parsed, allow_nan=False)
        return parsed
    if isinstance(type_, String):
        if not isinstance(value, str):
            raise SeedError("text fields must contain strings")
        if type_.length is not None and len(value) > type_.length:
            raise SeedError(f"text exceeds {type_.length} characters")
        return value
    raise SeedError(f"Unsupported column type for {column.name}")


def _parse_row(table_name: str, raw: Mapping[str, Any], *, is_csv: bool) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise SeedError("row must be an object")
    table = Base.metadata.tables[table_name]
    unknown = set(raw) - set(table.c.keys())
    if unknown:
        raise SeedError(f"unknown columns: {', '.join(sorted(unknown))}")
    try:
        row = {
            name: _parse_value(value, table.c[name], is_csv=is_csv)
            for name, value in raw.items()
            if not (is_csv and value == "" and table.c[name].nullable)
        }
    except (TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as exc:
        raise SeedError(str(exc)) from exc
    missing_key = [name for name in TABLE_KEYS[table_name] if row.get(name) is None]
    if missing_key:
        raise SeedError(f"missing natural key: {', '.join(missing_key)}")
    if "id" in table.c and row.get("id") is None:
        if "id" in TABLE_KEYS[table_name]:
            raise SeedError("id is required for assets and cases")
        identity = json.dumps([str(row[name]) for name in TABLE_KEYS[table_name]])
        row["id"] = uuid5(NAMESPACE_URL, f"meterdetective:{table_name}:{identity}")
    for column in table.c:
        if (
            not column.nullable
            and column.name not in row
            and column.default is None
            and column.server_default is None
        ):
            raise SeedError(f"missing required column: {column.name}")
        if not column.nullable and column.name in row and row[column.name] is None:
            raise SeedError(f"null required column: {column.name}")
    return row


def _key(row: Mapping[str, Any], names: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(_canonical(row[name]) for name in names)


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value


def _matches(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    return all(
        _canonical(existing.get(name)) == _canonical(value) for name, value in incoming.items()
    )


def _insert_batch(
    connection: Connection,
    table_name: str,
    batch: list[dict[str, Any]],
    seen: dict[tuple[Any, ...], dict[str, Any]],
    totals: Counter[str],
) -> None:
    table = Base.metadata.tables[table_name]
    names = TABLE_KEYS[table_name]
    unique_rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in batch:
        key = _key(row, names)
        previous = seen.get(key)
        if previous is not None:
            if not _matches(previous, row):
                raise SeedError(f"conflicting duplicate in {table_name}: {key}")
            totals["skipped"] += 1
            continue
        seen[key] = row
        unique_rows[key] = row
    if not unique_rows:
        return
    columns = [table.c[name] for name in names]
    keys = list(unique_rows)
    predicate = (
        columns[0].in_([key[0] for key in keys])
        if len(columns) == 1
        else sql_tuple(*columns).in_(keys)
    )
    existing = {
        _key(row, names): row
        for row in connection.execute(select(table).where(predicate)).mappings()
    }
    pending = []
    for key, row in unique_rows.items():
        current = existing.get(key)
        if current is None:
            pending.append(row)
        elif not _matches(current, row):
            raise SeedError(f"existing {table_name} row differs at key {key}")
        else:
            totals["skipped"] += 1
    if pending:
        # Executemany requires each parameter set to have the same columns.
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in pending:
            groups.setdefault(tuple(sorted(row)), []).append(row)
        for group in groups.values():
            connection.execute(insert(table), group)
        totals["inserted"] += len(pending)


def import_manifest(connection: Connection, manifest_path: Path) -> dict[str, dict[str, int]]:
    """Import one manifest within the caller's transaction."""
    tables = _read_manifest(manifest_path)
    result: dict[str, dict[str, int]] = {}
    for table_name in TABLE_KEYS:
        if table_name not in tables:
            continue
        totals: Counter[str] = Counter()
        seen: dict[tuple[Any, ...], dict[str, Any]] = {}
        batch: list[dict[str, Any]] = []
        for number, raw, is_csv in _rows(tables[table_name], manifest_path.parent):
            try:
                batch.append(_parse_row(table_name, raw, is_csv=is_csv))
            except SeedError as exc:
                raise SeedError(f"{table_name} row {number}: {exc}") from exc
            if len(batch) == BATCH_SIZE:
                _insert_batch(connection, table_name, batch, seen, totals)
                batch.clear()
        if batch:
            _insert_batch(connection, table_name, batch, seen, totals)
        result[table_name] = {"inserted": totals["inserted"], "skipped": totals["skipped"]}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a repeatable MeterDetective seed")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        with engine.begin() as connection:
            revision = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            if revision != head:
                raise SeedError(f"Database revision is {revision}; expected {head}")
            result = import_manifest(connection, args.manifest)
    except (SeedError, SQLAlchemyError) as exc:
        print(f"Seed import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
