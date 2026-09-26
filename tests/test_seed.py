import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select

from app.db.base import Base
from app.seed import TABLE_KEYS, SeedError, import_manifest

FIXTURE = Path("data/seed/day2b.json")
EXPECTED_COUNTS = {
    "assets": 3,
    "meters": 3,
    "readings": 6,
    "transformer_readings": 2,
    "events": 1,
    "anomalies": 1,
    "cases": 1,
    "case_meters": 1,
    "tariffs": 1,
}


@pytest.fixture
def seed_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _record):  # noqa: ANN001
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in TABLE_KEYS])
    yield engine
    engine.dispose()


def test_seed_is_repeatable_and_preserves_relationships(seed_engine) -> None:
    with seed_engine.begin() as connection:
        first = import_manifest(connection, FIXTURE)
        second = import_manifest(connection, FIXTURE)

        for table_name, expected in EXPECTED_COUNTS.items():
            table = Base.metadata.tables[table_name]
            assert first[table_name] == {"inserted": expected, "skipped": 0}
            assert second[table_name] == {"inserted": 0, "skipped": expected}
            assert connection.scalar(select(func.count()).select_from(table)) == expected

        meters = Base.metadata.tables["meters"]
        assets = Base.metadata.tables["assets"]
        cases = Base.metadata.tables["cases"]
        case_meters = Base.metadata.tables["case_meters"]
        assert connection.scalar(select(func.count()).select_from(meters.join(assets))) == 3
        assert connection.scalar(select(func.count()).select_from(cases.join(case_meters))) == 1


def test_conflicting_rerun_rolls_back_earlier_new_rows(seed_engine, tmp_path: Path) -> None:
    with seed_engine.begin() as connection:
        import_manifest(connection, FIXTURE)

    altered = json.loads(FIXTURE.read_text(encoding="utf-8"))
    altered["tables"]["meters"].append(
        {
            "id": "MD-004",
            "type": "residential",
            "status": "active",
            "transformer_id": "10000000-0000-4000-8000-000000000003",
        }
    )
    altered["tables"]["readings"][0]["kwh"] = 99.0
    path = tmp_path / "conflict.json"
    path.write_text(json.dumps(altered), encoding="utf-8")

    with (
        pytest.raises(SeedError, match="existing readings row differs"),
        seed_engine.begin() as connection,
    ):
        import_manifest(connection, path)

    with seed_engine.connect() as connection:
        meters = Base.metadata.tables["meters"]
        assert connection.scalar(select(func.count()).select_from(meters)) == 3


def test_csv_readings_accept_timezone_and_reject_conflicting_duplicates(
    seed_engine, tmp_path: Path
) -> None:
    with seed_engine.begin() as connection:
        import_manifest(connection, FIXTURE)

    csv_path = tmp_path / "readings.csv"
    csv_path.write_text(
        "meter_id,timestamp,kwh,source\n"
        "MD-001,2026-09-21T09:00:00Z,1.1,synthetic_seed\n"
        "MD-001,2026-09-21T09:00:00Z,1.1,synthetic_seed\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"format_version": 1, "tables": {"readings": {"file": "readings.csv"}}}),
        encoding="utf-8",
    )

    with seed_engine.begin() as connection:
        assert import_manifest(connection, manifest_path)["readings"] == {
            "inserted": 1,
            "skipped": 1,
        }

    csv_path.write_text(
        "meter_id,timestamp,kwh,source\n"
        "MD-001,2026-09-21T09:00:00Z,1.1,synthetic_seed\n"
        "MD-001,2026-09-21T09:00:00Z,2.0,synthetic_seed\n",
        encoding="utf-8",
    )
    with pytest.raises(SeedError, match="conflicting duplicate"), seed_engine.begin() as connection:
        import_manifest(connection, manifest_path)
