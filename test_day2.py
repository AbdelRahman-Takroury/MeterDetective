import os
import pandas as pd


# ============================================================
# الملفات التي يجب أن ينتجها Day 2
# ============================================================

REQUIRED_FILES = [

    "seed_data.csv",

    "topology.csv",

    "injected_readings.csv",

    "transformer_readings.csv",

    "ground_truth.csv",

    "customer_metadata.csv",

    "jod_tariff.json",

    "historical_alerts.csv"
]


# ============================================================
# Test 1
# التأكد أن كل الملفات موجودة
# ============================================================

def test_required_files_exist():

    for file_name in REQUIRED_FILES:

        assert os.path.exists(
            file_name
        ), f"Missing file: {file_name}"


# ============================================================
# Test 2
# topology بدون duplicate meters
# ============================================================

def test_topology_unique_meters():

    topology = pd.read_csv(
        "topology.csv"
    )

    assert (
        topology["Meter_ID"]
        .is_unique
    )


# ============================================================
# Test 3
# topology تحتوي على 6 transformers
# ============================================================

def test_topology_transformers():

    topology = pd.read_csv(
        "topology.csv"
    )

    assert (
        topology["Transformer_ID"]
        .nunique()
        == 6
    )


# ============================================================
# Test 4
# topology تحتوي على 3 feeders
# ============================================================

def test_topology_feeders():

    topology = pd.read_csv(
        "topology.csv"
    )

    assert (
        topology["Feeder_ID"]
        .nunique()
        == 3
    )


# ============================================================
# Test 5
# التأكد أن ground truth منفصل
# ============================================================

def test_ground_truth_exists():

    ground_truth = pd.read_csv(
        "ground_truth.csv"
    )

    assert len(
        ground_truth
    ) >= 5


# ============================================================
# Test 6
# التأكد من وجود أكثر من نوع scenario
# ============================================================

def test_multiple_scenario_types():

    ground_truth = pd.read_csv(
        "ground_truth.csv"
    )

    assert (
        ground_truth["Anomaly_Type"]
        .nunique()
        >= 5
    )


# ============================================================
# Test 7
# التأكد من وجود Local و Shared
# ============================================================

def test_local_and_shared_scenarios():

    ground_truth = pd.read_csv(
        "ground_truth.csv"
    )

    scopes = set(
        ground_truth["Scope"]
    )

    assert "Local" in scopes
    assert "Shared" in scopes


# ============================================================
# Test 8
# customer metadata
# ============================================================

def test_customer_metadata():

    metadata = pd.read_csv(
        "customer_metadata.csv"
    )

    assert "Meter_ID" in metadata.columns
    assert "Has_Solar" in metadata.columns
    assert "Has_EV" in metadata.columns


# ============================================================
# Test 9
# transformer readings
# ============================================================

def test_transformer_readings():

    transformer = pd.read_csv(
        "transformer_readings.csv"
    )

    assert (
        "Transformer_ID"
        in transformer.columns
    )

    assert (
        "Transformer_Reading"
        in transformer.columns
    )


# ============================================================
# Test 10
# historical cases
# ============================================================

def test_historical_cases():

    history = pd.read_csv(
        "historical_alerts.csv"
    )

    assert (
        len(history) > 0
    )

    assert (
        "Status"
        in history.columns
    )
