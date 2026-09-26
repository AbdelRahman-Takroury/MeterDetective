"""Integration checks for the reproducible Developer A fixture pipeline."""

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.rebuild_developer_a import READING_FIELD, rebuild
from experiments.developer_a_agent import (
    get_historical_alerts,
    get_tariff_info,
    get_transformer_data,
)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_rebuild_is_repeatable_and_keeps_truth_separate(tmp_path: Path) -> None:
    raw = tmp_path / "raw.csv"
    start = datetime(2012, 5, 1)
    with raw.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["LCLid", "stdorToU", "DateTime", READING_FIELD])
        for day in (0, 15, 18):
            for meter in ("A", "B"):
                writer.writerow([meter, "Std", (start + timedelta(days=day)).isoformat(), 10])

    published = tmp_path / "published"
    published.mkdir()
    tariff = {"currency": "JOD", "brackets": [{"rate": 0.1}]}
    (published / "jod_tariff.json").write_text(json.dumps(tariff), encoding="utf-8")

    first = tmp_path / "first"
    second = tmp_path / "second"
    first_truth = tmp_path / "evaluation" / "first.csv"
    second_truth = tmp_path / "evaluation" / "second.csv"
    first_profile = rebuild(raw, published, first, truth_path=first_truth)
    second_profile = rebuild(raw, published, second, truth_path=second_truth)

    assert first_profile == second_profile
    assert first_profile["meter_count"] == 10
    assert first_profile["reading_count"] == 30
    assert first_truth.read_bytes() == second_truth.read_bytes()
    assert not (first / "ground_truth.csv").exists()
    assert len(_rows(first_truth)) == 2

    truth = _rows(first_truth)
    readings = _rows(first / "seed_data.csv")
    local = next(row for row in truth if row["Anomaly_Type"] == "Local-Drop-70pct")
    target = local["Target_ID"]
    values = {
        row["DateTime"][:10]: float(row[READING_FIELD])
        for row in readings
        if row["LCLid"] == target
    }
    assert values["2012-05-01"] == 10
    assert values["2012-05-16"] == 3

    topology = {row["Meter_ID"]: row["Transformer_ID"] for row in _rows(first / "topology.csv")}
    shared_readings = [
        row for row in readings
        if topology[row["LCLid"]] == "TX_3"
        and row["DateTime"].startswith("2012-05-19")
    ]
    assert shared_readings
    assert all(5.7 <= float(row[READING_FIELD]) <= 6.3 for row in shared_readings)

    assert get_tariff_info(first) == tariff
    assert get_transformer_data("TX_3", first)
    assert isinstance(get_historical_alerts("TX_3", first), list)

    with pytest.raises(ValueError, match="Ground truth must be outside"):
        rebuild(raw, published, first, truth_path=first / "ground_truth.csv")
