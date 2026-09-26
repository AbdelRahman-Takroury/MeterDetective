"""Normalize Developer A's published CSV fixtures for app.seed."""

import argparse
import csv
import io
import json
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


def _text(filename: str, *, source_dir: Path | None, ref: str | None) -> str:
    if source_dir is not None:
        return (source_dir / filename).read_text(encoding="utf-8-sig")
    result = subprocess.run(
        ["git", "show", f"{ref}:{filename}"],
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"Cannot read {filename} from Git ref {ref}: {result.stderr.strip()}")
    return result.stdout


def _csv(filename: str, *, source_dir: Path | None, ref: str | None) -> csv.DictReader:
    return csv.DictReader(io.StringIO(_text(filename, source_dir=source_dir, ref=ref)))


def _uuid(kind: str, external_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"meterdetective:developer-a:{kind}:{external_id}"))


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def prepare(
    output_dir: Path, *, source_dir: Path | None = None, ref: str | None = None
) -> dict[str, int]:
    if (source_dir is None) == (ref is None):
        raise ValueError("Provide exactly one of source_dir or ref")

    topology = list(_csv("topology.csv", source_dir=source_dir, ref=ref))
    metadata = {
        row["Meter_ID"]: row
        for row in _csv("customer_metadata.csv", source_dir=source_dir, ref=ref)
    }
    if not topology or len({row["Meter_ID"] for row in topology}) != len(topology):
        raise ValueError("Topology must contain unique meter IDs")
    meter_ids = {row["Meter_ID"] for row in topology}
    if meter_ids != set(metadata):
        raise ValueError("Customer metadata must contain exactly the topology meters")

    substations = sorted({row["Substation_ID"] for row in topology})
    feeders = {row["Feeder_ID"]: row["Substation_ID"] for row in topology}
    transformers = {row["Transformer_ID"]: row["Feeder_ID"] for row in topology}
    if any(feeders[row["Feeder_ID"]] != row["Substation_ID"] for row in topology):
        raise ValueError("A feeder has more than one parent substation")
    if any(transformers[row["Transformer_ID"]] != row["Feeder_ID"] for row in topology):
        raise ValueError("A transformer has more than one parent feeder")

    assets = [
        {
            "id": _uuid("substation", name),
            "asset_type": "substation",
            "name": name,
            "metadata_json": {"external_id": name, "synthetic": True},
        }
        for name in substations
    ]
    assets.extend(
        {
            "id": _uuid("feeder", name),
            "parent_id": _uuid("substation", parent),
            "asset_type": "feeder",
            "name": name,
            "metadata_json": {"external_id": name, "synthetic": True},
        }
        for name, parent in sorted(feeders.items())
    )
    assets.extend(
        {
            "id": _uuid("transformer", name),
            "parent_id": _uuid("feeder", parent),
            "asset_type": "transformer",
            "name": name,
            "metadata_json": {"external_id": name, "synthetic": True},
        }
        for name, parent in sorted(transformers.items())
    )
    meters_by_transformer: dict[str, list[str]] = defaultdict(list)
    meters = []
    for row in sorted(topology, key=lambda item: item["Meter_ID"]):
        meter_id = row["Meter_ID"]
        transformer = row["Transformer_ID"]
        context = metadata[meter_id]
        meters_by_transformer[transformer].append(meter_id)
        meters.append(
            {
                "id": meter_id,
                "transformer_id": _uuid("transformer", transformer),
                "type": "smart_meter",
                "status": "active",
                "customer_segment": context["Customer_Type"].lower(),
                "has_solar": context["Has_Solar"].lower() == "true",
                "has_ev": context["Has_EV"].lower() == "true",
                "metadata_source": "developer_a:customer_metadata.csv",
                "metadata_json": {"synthetic": True, "source_meter_id": meter_id},
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    reading_count = 0
    with (output_dir / "readings.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["meter_id", "timestamp", "kwh", "source"])
        writer.writeheader()
        for row in _csv("seed_data.csv", source_dir=source_dir, ref=ref):
            meter_id = row["LCLid"]
            if meter_id not in meter_ids:
                raise ValueError(f"Reading references unknown meter {meter_id}")
            writer.writerow(
                {
                    "meter_id": meter_id,
                    "timestamp": _timestamp(row["DateTime"]),
                    "kwh": row["KWH/hh (per half hour)"],
                    "source": "developer_a:seed_data.csv",
                }
            )
            reading_count += 1

    transformer_count = 0
    with (output_dir / "transformer_readings.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=["transformer_id", "timestamp", "input_kwh"])
        writer.writeheader()
        for row in _csv("transformer_readings.csv", source_dir=source_dir, ref=ref):
            transformer = row["Transformer_ID"]
            if transformer not in transformers:
                raise ValueError(f"Reading references unknown transformer {transformer}")
            writer.writerow(
                {
                    "transformer_id": _uuid("transformer", transformer),
                    "timestamp": _timestamp(row["DateTime"]),
                    "input_kwh": row["Transformer_Reading"],
                }
            )
            transformer_count += 1

    events = []
    cases = []
    case_meters = []
    for row in _csv("historical_alerts.csv", source_dir=source_dir, ref=ref):
        ticket_id = row["Ticket_ID"]
        transformer = row["Equipment_ID"]
        if transformer not in transformers:
            raise ValueError(f"Historical ticket references unknown transformer {transformer}")
        event_id = _uuid("historical_event", ticket_id)
        case_id = _uuid("historical_case", ticket_id)
        occurred_at = _timestamp(row["Date_Opened"])
        events.append(
            {
                "id": event_id,
                "idempotency_key": f"developer-a:ticket:{ticket_id}",
                "event_type": "investigation.requested",
                "payload_json": {
                    "event_id": event_id,
                    "event_type": "investigation.requested",
                    "occurred_at": occurred_at,
                    "transformer_id": _uuid("transformer", transformer),
                    "source": "developer_a:historical_alerts.csv",
                    "schema_version": "1.0",
                    "data": {"ticket_id": ticket_id, "issue_type": row["Issue_Type"]},
                },
                "status": "processed",
                "created_at": occurred_at,
            }
        )
        cases.append(
            {
                "id": case_id,
                "title": f"{ticket_id}: {row['Issue_Type']} ({transformer})",
                "status": row["Status"].lower(),
                "opened_at": occurred_at,
                "updated_at": occurred_at,
            }
        )
        case_meters.extend(
            {
                "case_id": case_id,
                "meter_id": meter_id,
                "relationship": "transformer_context",
            }
            for meter_id in meters_by_transformer[transformer]
        )

    tariff = json.loads(_text("jod_tariff.json", source_dir=source_dir, ref=ref))
    if tariff["currency"] != "JOD":
        raise ValueError("Developer A tariff must be denominated in JOD")
    year = tariff["version"].split("-", 1)[0]
    tariff_rows = [
        {
            "name": (
                f"{tariff['type']} {tariff['version']} "
                f"{bracket['min_kwh']}-{bracket['max_kwh']} kWh"
            ),
            "customer_segment": "residential",
            "currency": "JOD",
            "jod_per_kwh": str(bracket["rate_per_kwh"]),
            "effective_from": f"{year}-01-01T00:00:00Z",
            "source": "developer_a:jod_tariff.json",
            "is_synthetic": True,
        }
        for bracket in tariff["brackets"]
    ]

    manifest = {
        "format_version": 1,
        "tables": {
            "assets": assets,
            "meters": meters,
            "readings": {"file": "readings.csv"},
            "transformer_readings": {"file": "transformer_readings.csv"},
            "events": events,
            "cases": cases,
            "case_meters": case_meters,
            "tariffs": tariff_rows,
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "assets": len(assets),
        "meters": len(meters),
        "readings": reading_count,
        "transformer_readings": transformer_count,
        "events": len(events),
        "cases": len(cases),
        "case_meters": len(case_meters),
        "tariffs": len(tariff_rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize Developer A fixtures for app.seed")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-dir", type=Path)
    source.add_argument("--ref")
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/developer_a"))
    args = parser.parse_args(argv)
    try:
        counts = prepare(args.output_dir, source_dir=args.source_dir, ref=args.ref)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Fixture preparation failed: {exc}\n")
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
