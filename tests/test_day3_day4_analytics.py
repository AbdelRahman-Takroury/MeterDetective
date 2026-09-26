from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.tools.analytics import (
    AnomalyInput,
    AssetNode,
    BaselineInput,
    ConnectedAssetsInput,
    MeterConnection,
    MeterSeries,
    PeerComparisonInput,
    PeerSelectionInput,
    QualityInput,
    ReadingPoint,
    SharedIncidentInput,
    calculate_baseline,
    compare_with_peers,
    detect_anomaly,
    detect_shared_incident,
    get_connected_assets,
    select_dynamic_peers,
    validate_reading_quality,
)


def point(at: datetime, value: float | None) -> ReadingPoint:
    return ReadingPoint(timestamp=at, kwh=value)


def test_quality_detects_duplicate_gap_impossible_and_flatline() -> None:
    start = datetime(2026, 9, 20, tzinfo=UTC)
    readings = [
        point(start, 1),
        point(start, 1),
        point(start + timedelta(minutes=30), 1),
        point(start + timedelta(minutes=60), 1),
        point(start + timedelta(minutes=120), -1),
        point(start + timedelta(minutes=150), None),
    ]
    result = validate_reading_quality(QualityInput(run_id=uuid4(), readings=readings))

    assert result.status == "critical"
    assert result.reliable is False
    assert result.metrics.duplicate_timestamps == 1
    assert result.metrics.missing_intervals == 1
    assert result.metrics.negative_values == 1
    assert result.metrics.missing_values == 1
    assert result.metrics.max_flatline_run >= 3


def baseline_for(event_time: datetime):
    history = [
        point(event_time - timedelta(days=7 * week), value)
        for week, value in enumerate((10.0, 10.2, 9.8, 10.1), start=1)
    ]
    result = calculate_baseline(
        BaselineInput(
            run_id=uuid4(),
            readings=history + [point(event_time, 1000)],
            cutoff=event_time,
        )
    )
    assert result.status == "success"
    assert result.excluded_future_readings == 1
    return result


def test_baseline_is_half_hourly_and_excludes_event_and_future_data() -> None:
    event_time = datetime(2026, 9, 20, 12, 30, tzinfo=UTC)
    result = baseline_for(event_time)

    assert len(result.profiles) == 1
    assert result.profiles[0].minute == 30
    assert 9.8 <= result.profiles[0].median_kwh <= 10.2


def test_anomaly_detects_drop_spike_gap_zero_and_flatline_without_false_normal() -> None:
    event_time = datetime(2026, 9, 20, 12, 30, tzinfo=UTC)
    baseline = baseline_for(event_time)
    for value, expected in ((3.0, "drop"), (17.0, "spike"), (0.0, "zero_period")):
        result = detect_anomaly(
            AnomalyInput(
                run_id=uuid4(),
                readings=[point(event_time, value)],
                baseline_profiles=baseline.profiles,
            )
        )
        assert expected in {item.anomaly_type for item in result.events}

    gap = detect_anomaly(
        AnomalyInput(
            run_id=uuid4(),
            readings=[point(event_time, None)],
            baseline_profiles=baseline.profiles,
        )
    )
    assert gap.events[0].anomaly_type == "gap"

    flatline = detect_anomaly(
        AnomalyInput(
            run_id=uuid4(),
            readings=[
                point(event_time + timedelta(minutes=30 * index), 10.0) for index in range(3)
            ],
            baseline_profiles=baseline.profiles,
        )
    )
    assert "flatline" in {item.anomaly_type for item in flatline.events}

    normal = detect_anomaly(
        AnomalyInput(
            run_id=uuid4(),
            readings=[point(event_time, 10.0)],
            baseline_profiles=baseline.profiles,
        )
    )
    assert normal.status == "normal"


def series(
    meter_id: str,
    event_time: datetime,
    values: list[float],
    current: float,
    *,
    segment: str = "residential",
) -> MeterSeries:
    start = event_time - timedelta(minutes=30 * len(values))
    readings = [
        point(start + timedelta(minutes=30 * index), value)
        for index, value in enumerate(values)
    ]
    readings.append(point(event_time, current))
    return MeterSeries(
        meter_id=meter_id,
        customer_segment=segment,
        has_solar=False,
        has_ev=False,
        readings=readings,
    )


def test_peer_selection_uses_pre_event_overlap_metadata_and_deterministic_order() -> None:
    event_time = datetime(2026, 9, 20, 12, tzinfo=UTC)
    shape = [float(index % 5 + 1) for index in range(20)]
    target = series("M1", event_time, shape, 3)
    good = series("M2", event_time, [value * 2 for value in shape], 10)
    other_segment = series("M3", event_time, shape, 10, segment="commercial")
    future = point(event_time + timedelta(minutes=30), 99_999)
    good.readings.append(future)

    selected = select_dynamic_peers(
        PeerSelectionInput(
            run_id=uuid4(),
            target=target,
            candidates=[other_segment, good],
            event_time=event_time,
        )
    )

    assert [item.meter_id for item in selected.peers] == ["M2"]
    compared = compare_with_peers(
        PeerComparisonInput(
            run_id=uuid4(), target=target, peers=[good], event_time=event_time
        )
    )
    assert compared.status == "answered"
    assert compared.target_kwh == 3
    assert compared.peer_median_kwh == 10


def test_topology_traversal_and_shared_local_boundary() -> None:
    event_time = datetime(2026, 9, 20, 12, tzinfo=UTC)
    topology = get_connected_assets(
        ConnectedAssetsInput(
            run_id=uuid4(),
            target_meter_id="M1",
            assets=[
                AssetNode(asset_id="S1", asset_type="substation"),
                AssetNode(asset_id="F1", asset_type="feeder", parent_id="S1"),
                AssetNode(asset_id="T1", asset_type="transformer", parent_id="F1"),
            ],
            meters=[
                MeterConnection(meter_id="M1", transformer_id="T1"),
                MeterConnection(meter_id="M2", transformer_id="T1"),
                MeterConnection(meter_id="M3", transformer_id="T1"),
            ],
        )
    )
    assert topology.transformer_id == "T1"
    assert topology.feeder_id == "F1"
    assert topology.substation_id == "S1"
    assert topology.connected_meter_ids == ["M2", "M3"]

    history = [10.0] * 6
    one_drop = [
        series("M2", event_time, history, 3),
        series("M3", event_time, history, 10),
    ]
    boundary = detect_shared_incident(
        SharedIncidentInput(
            run_id=uuid4(), connected_series=one_drop, event_time=event_time
        )
    )
    assert boundary.incident_type == "shared"  # 1/2 meets the documented >= 50% rule.

    local = detect_shared_incident(
        SharedIncidentInput(
            run_id=uuid4(),
            connected_series=one_drop,
            event_time=event_time,
            shared_threshold=0.6,
        )
    )
    assert local.incident_type == "local"

    normal_peers = detect_shared_incident(
        SharedIncidentInput(
            run_id=uuid4(),
            connected_series=[
                series("M2", event_time, history, 10),
                series("M3", event_time, history, 10),
            ],
            event_time=event_time,
        )
    )
    assert normal_peers.incident_type == "local"
