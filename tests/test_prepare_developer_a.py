import csv
import json
from pathlib import Path

from app.prepare_developer_a import prepare


def test_developer_a_export_is_deterministic_and_preserves_links(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "topology.csv").write_text(
        "Meter_ID,Transformer_ID,Feeder_ID,Substation_ID\n"
        "M-2,TX_1,Feeder_1,Sub_1\nM-1,TX_1,Feeder_1,Sub_1\n",
        encoding="utf-8",
    )
    (source / "customer_metadata.csv").write_text(
        "Meter_ID,Has_Solar,Has_EV,Customer_Type\n"
        "M-1,True,False,Residential\nM-2,False,True,Residential\n",
        encoding="utf-8",
    )
    (source / "seed_data.csv").write_text(
        "LCLid,stdorToU,DateTime,KWH/hh (per half hour)\n"
        "M-1,Std,2012-05-21 10:30:00,0.2\nM-2,Std,2012-05-21 10:30:00,0.3\n",
        encoding="utf-8",
    )
    (source / "transformer_readings.csv").write_text(
        "Transformer_ID,DateTime,KWH/hh (per half hour),Transformer_Reading\n"
        "TX_1,2012-05-21 10:30:00,0.5,0.53\n",
        encoding="utf-8",
    )
    (source / "historical_alerts.csv").write_text(
        "Ticket_ID,Equipment_ID,Issue_Type,Date_Opened,Status\n"
        "TKT_1,TX_1,Voltage Drop,2012-10-07,Closed\n",
        encoding="utf-8",
    )
    (source / "jod_tariff.json").write_text(
        json.dumps(
            {
                "currency": "JOD",
                "type": "Residential_Block",
                "version": "2026-v1",
                "brackets": [{"min_kwh": 0, "max_kwh": 300, "rate_per_kwh": 0.05}],
                "fixed_charges": 1.5,
            }
        ),
        encoding="utf-8",
    )

    first = tmp_path / "first"
    second = tmp_path / "second"
    expected = {
        "assets": 3,
        "meters": 2,
        "readings": 2,
        "transformer_readings": 1,
        "events": 1,
        "cases": 1,
        "case_meters": 2,
        "tariffs": 1,
    }
    assert prepare(first, source_dir=source) == expected
    assert prepare(second, source_dir=source) == expected
    for filename in ("manifest.json", "readings.csv", "transformer_readings.csv"):
        assert (first / filename).read_bytes() == (second / filename).read_bytes()

    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    tables = manifest["tables"]
    transformer = next(asset for asset in tables["assets"] if asset["asset_type"] == "transformer")
    assert {meter["transformer_id"] for meter in tables["meters"]} == {transformer["id"]}
    assert {link["meter_id"] for link in tables["case_meters"]} == {"M-1", "M-2"}
    assert tables["events"][0]["payload_json"]["transformer_id"] == transformer["id"]
    with (first / "readings.csv").open(encoding="utf-8", newline="") as stream:
        readings = list(csv.DictReader(stream))
    assert {reading["timestamp"] for reading in readings} == {"2012-05-21T10:30:00Z"}
