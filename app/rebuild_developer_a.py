"""Deterministically rebuild Developer A's synthetic fixtures from the LCL sample."""

import argparse
import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from shutil import copyfile

from app.prepare_developer_a import prepare

RAW_DEFAULT = Path("data/source/developer_a/LCL-June2015v2_98.csv")
PUBLISHED_DEFAULT = Path("data/fixtures/developer_a")
OUTPUT_DEFAULT = Path("data/processed/rebuilt_developer_a")
TRUTH_DEFAULT = Path("data/evaluation/rebuilt_developer_a/ground_truth.csv")
READING_FIELD = "KWH/hh (per half hour)"
TRANSFORMERS = tuple(f"TX_{number}" for number in range(1, 7))
ISSUE_TYPES = ("Overheating", "Voltage Drop", "Communication Loss", "Tampering_Suspected")


def _source_rows(path: Path):  # noqa: ANN201
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, skipinitialspace=True)
        reader.fieldnames = [name.strip() for name in reader.fieldnames or []]
        for row in reader:
            try:
                meter_id = row["LCLid"].strip()
                tariff = row["stdorToU"].strip()
                stamp = datetime.fromisoformat(row["DateTime"].strip())
                kwh = float(row[READING_FIELD].strip())
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if meter_id and tariff and math.isfinite(kwh) and kwh >= 0:
                yield meter_id, tariff, stamp, kwh


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rebuild(
    raw_path: Path,
    published_dir: Path,
    output_dir: Path,
    *,
    truth_path: Path,
    seed: int = 42,
) -> dict[str, object]:
    output_root = output_dir.resolve()
    if output_root == published_dir.resolve() or raw_path.resolve().is_relative_to(output_root):
        raise ValueError("Rebuild output must not overwrite its source fixtures")
    if truth_path.resolve().is_relative_to(output_root):
        raise ValueError("Ground truth must be outside the agent-readable output")

    selected: list[str] = []
    selected_set: set[str] = set()
    first_at: datetime | None = None
    for meter_id, _, stamp, _ in _source_rows(raw_path):
        if meter_id not in selected_set and len(selected) < 150:
            selected.append(meter_id)
            selected_set.add(meter_id)
        first_at = stamp if first_at is None or stamp < first_at else first_at
    if first_at is None:
        raise ValueError("Raw source has no valid readings")
    last_at = first_at + timedelta(days=30)

    base: list[tuple[str, str, datetime, float]] = []
    seen: dict[tuple[str, datetime], float] = {}
    for meter_id, tariff, stamp, kwh in _source_rows(raw_path):
        if meter_id not in selected_set or not first_at <= stamp <= last_at:
            continue
        key = (meter_id, stamp)
        previous = seen.get(key)
        if previous is not None:
            if previous != kwh:
                raise ValueError(f"Conflicting source reading for {meter_id} at {stamp}")
            continue
        seen[key] = kwh
        base.append((meter_id, tariff, stamp, kwh))
    if not base:
        raise ValueError("No readings remain in the selected 30-day window")

    active_meters = list(dict.fromkeys(meter_id for meter_id, _, _, _ in base))
    meter_ids = active_meters + [
        f"{meter}_Copy{copy}" for copy in range(1, 5) for meter in active_meters
    ]
    topology_rng = random.Random(seed)
    topology_rng.shuffle(meter_ids)
    topology = {}
    group_size = math.ceil(len(meter_ids) / len(TRANSFORMERS))
    for position, meter_id in enumerate(meter_ids):
        transformer = TRANSFORMERS[min(position // group_size, len(TRANSFORMERS) - 1)]
        topology[meter_id] = transformer

    local_target = next(
        (meter for meter in active_meters if topology[meter] != "TX_3"), active_meters[0]
    )
    local_day = (first_at + timedelta(days=15)).date()
    shared_day = (first_at + timedelta(days=18)).date()
    output_dir.mkdir(parents=True, exist_ok=True)

    with (output_dir / "topology.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Meter_ID", "Transformer_ID", "Feeder_ID", "Substation_ID"])
        for meter_id, transformer in topology.items():
            number = int(transformer.split("_")[1])
            writer.writerow([meter_id, transformer, f"Feeder_{(number + 1) // 2}", "Sub_Main_1"])

    metadata_rng = random.Random(seed + 1)
    with (output_dir / "customer_metadata.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Meter_ID", "Has_Solar", "Has_EV", "Customer_Type"])
        for meter_id in topology:
            writer.writerow(
                [
                    meter_id,
                    metadata_rng.random() < 0.15,
                    metadata_rng.random() < 0.10,
                    "Residential",
                ]
            )

    consumption: dict[tuple[str, datetime], float] = defaultdict(float)
    reading_count = 0
    noise_rng = random.Random(seed + 2)
    with (output_dir / "seed_data.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["LCLid", "stdorToU", "DateTime", READING_FIELD])
        for copy in range(5):
            for source_meter, tariff, stamp, base_kwh in base:
                meter_id = source_meter if copy == 0 else f"{source_meter}_Copy{copy}"
                kwh = base_kwh if copy == 0 else base_kwh * noise_rng.uniform(0.95, 1.05)
                if meter_id == local_target and stamp.date() == local_day:
                    kwh *= 0.3
                if topology[meter_id] == "TX_3" and stamp.date() == shared_day:
                    kwh *= 0.6
                writer.writerow([meter_id, tariff, stamp.isoformat(sep=" "), format(kwh, ".12g")])
                consumption[(topology[meter_id], stamp)] += kwh
                reading_count += 1

    with (output_dir / "transformer_readings.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(["Transformer_ID", "DateTime", READING_FIELD, "Transformer_Reading"])
        for (transformer, stamp), kwh in sorted(consumption.items()):
            writer.writerow(
                [
                    transformer,
                    stamp.isoformat(sep=" "),
                    format(kwh, ".12g"),
                    format(kwh * 1.03, ".12g"),
                ]
            )

    copyfile(published_dir / "jod_tariff.json", output_dir / "jod_tariff.json")
    history_rng = random.Random(seed + 3)
    active_transformers = tuple(sorted(set(topology.values())))
    with (output_dir / "historical_alerts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Ticket_ID", "Equipment_ID", "Issue_Type", "Date_Opened", "Status"])
        for number in range(15):
            writer.writerow(
                [
                    f"TKT_{1000 + number}",
                    history_rng.choice(active_transformers),
                    history_rng.choice(ISSUE_TYPES),
                    (datetime(2012, 10, 7) + timedelta(days=7 * number)).date().isoformat(),
                    "Closed",
                ]
            )
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    with truth_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Target_ID", "Anomaly_Date", "Anomaly_Type"])
        writer.writerow([local_target, local_day.isoformat(), "Local-Drop-70pct"])
        writer.writerow(["TX_3", shared_day.isoformat(), "Shared-Drop-40pct"])

    files = (
        "seed_data.csv",
        "topology.csv",
        "customer_metadata.csv",
        "transformer_readings.csv",
        "historical_alerts.csv",
        "jod_tariff.json",
    )
    profile: dict[str, object] = {
        "seed": seed,
        "source_sha256": _sha256(raw_path),
        "meter_count": len(meter_ids),
        "reading_count": reading_count,
        "transformer_reading_count": len(consumption),
        "window_start": first_at.isoformat(),
        "window_end": last_at.isoformat(),
        "file_sha256": {name: _sha256(output_dir / name) for name in files},
        "ground_truth_sha256": _sha256(truth_path),
    }
    (output_dir / "profile.json").write_text(
        json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild deterministic Developer A fixtures")
    parser.add_argument("--raw", type=Path, default=RAW_DEFAULT)
    parser.add_argument("--published-dir", type=Path, default=PUBLISHED_DEFAULT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--truth-path", type=Path, default=TRUTH_DEFAULT)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    profile = rebuild(
        args.raw,
        args.published_dir,
        args.output_dir,
        truth_path=args.truth_path,
        seed=args.seed,
    )
    prepare(args.output_dir / "import", source_dir=args.output_dir)
    print(json.dumps(profile, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
