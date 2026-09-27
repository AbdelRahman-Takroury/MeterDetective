from datetime import UTC, datetime, timedelta

import plotly.graph_objects as go

from app.services.charts import (
    build_peer_comparison_chart,
    build_readings_baseline_anomaly_chart,
    build_weather_context_chart,
)
from app.tools.advanced import ForecastOutput
from app.tools.analytics import (
    AnomalyEvent,
    AnomalyOutput,
    BaselineOutput,
    BaselineProfile,
    MeterSeries,
    PeerComparisonOutput,
    ReadingPoint,
)
from app.tools.weather import WeatherContextOutput, WeatherObservation


def _scenario_inputs():
    event_time = datetime(2026, 9, 20, 12, tzinfo=UTC)
    readings = [
        ReadingPoint(timestamp=event_time - timedelta(minutes=30), kwh=9.7),
        ReadingPoint(timestamp=event_time, kwh=3.0),
        ReadingPoint(timestamp=event_time + timedelta(minutes=30), kwh=None),
    ]
    baseline = BaselineOutput(
        status="success",
        profiles=[
            BaselineProfile(
                weekday=event_time.weekday(), hour=12, minute=0, sample_count=4,
                median_kwh=9.7, q1_kwh=9.7, q3_kwh=9.7, iqr_kwh=0.0,
            ),
            BaselineProfile(
                weekday=event_time.weekday(), hour=12, minute=30, sample_count=4,
                median_kwh=9.75, q1_kwh=9.75, q3_kwh=9.75, iqr_kwh=0.0,
            ),
        ],
    )
    event = AnomalyEvent(
        timestamp=event_time, anomaly_type="drop", observed_kwh=3.0,
        expected_kwh=9.7, severity=69.07,
        components={"deviation_pct": 69.07, "lower_bound": 8.73},
    )
    anomalies = AnomalyOutput(
        status="anomalies_detected", events=[event], overall_severity=69.07,
    )
    forecast = ForecastOutput(
        status="answered", expected_kwh=9.7,
        method="seasonal_weekday_halfhour", support_count=4,
    )
    target = MeterSeries(meter_id="M1", readings=readings[:2])
    peers = (
        MeterSeries(meter_id="M2", readings=[
            ReadingPoint(timestamp=event_time - timedelta(minutes=30), kwh=10.3),
            ReadingPoint(timestamp=event_time, kwh=10.4),
        ]),
        MeterSeries(meter_id="M3", readings=[
            ReadingPoint(timestamp=event_time - timedelta(minutes=30), kwh=10.7),
            ReadingPoint(timestamp=event_time, kwh=10.8),
        ]),
    )
    comparison = PeerComparisonOutput(
        status="answered", target_kwh=3.0, peer_count=2,
        peer_median_kwh=10.6, deviation_pct=-71.7, percentile=0.0,
    )
    return event_time, readings, baseline, anomalies, forecast, target, peers, comparison


def test_readings_chart_uses_supplied_values_and_preserves_missing():
    event_time, readings, baseline, anomalies, forecast, *_ = _scenario_inputs()
    figure = build_readings_baseline_anomaly_chart(
        readings, baseline=baseline, anomalies=anomalies,
        forecast=forecast, forecast_timestamp=event_time,
    )
    assert isinstance(figure, go.Figure)
    assert list(figure.data[0].y) == [9.7, 3.0, None]
    assert list(figure.data[1].y) == [None, 9.7, 9.75]
    assert list(figure.data[2].y) == [9.7]
    assert list(figure.data[3].y) == [3.0]
    assert list(figure.data[3].x) == [event_time]
    assert list(figure.data[3].customdata[0]) == ["drop", 69.07, 9.7]


def test_peer_chart_uses_supplied_target_and_selected_peer_series():
    _, _, _, _, _, target, peers, comparison = _scenario_inputs()
    figure = build_peer_comparison_chart(target, peers, comparison=comparison)
    assert isinstance(figure, go.Figure)
    assert [trace.name for trace in figure.data] == ["Target M1", "Peer M2", "Peer M3"]
    assert list(figure.data[0].y) == [9.7, 3.0]
    assert list(figure.data[1].y) == [10.3, 10.4]
    assert list(figure.data[2].y) == [10.7, 10.8]
    assert "-71.7%" in figure.layout.annotations[0].text


def test_unavailable_weather_has_empty_unavailable_state():
    weather = WeatherContextOutput(
        status="unavailable", source_url="https://weather.invalid", observations=[],
        fetched_at=datetime(2026, 9, 20, tzinfo=UTC), confidence=0.0,
    )
    figure = build_weather_context_chart(weather)
    assert isinstance(figure, go.Figure)
    assert len(figure.data) == 0
    assert figure.layout.annotations[0].text == "Weather unavailable"
    assert len(build_weather_context_chart(None).data) == 0


def test_weather_chart_plots_only_supplied_values_and_keeps_missing_precipitation():
    timestamp = datetime(2026, 9, 20, 12, tzinfo=UTC)
    weather = WeatherContextOutput(
        status="answered", source_url="fixture", fetched_at=timestamp, confidence=1.0,
        observations=[
            WeatherObservation(timestamp=timestamp, temperature_c=31.0, precipitation_mm=None),
            WeatherObservation(
                timestamp=timestamp + timedelta(hours=1),
                temperature_c=32.0,
                precipitation_mm=0.4,
            ),
        ],
    )
    figure = build_weather_context_chart(weather)
    assert len(figure.data) == 2
    assert list(figure.data[0].y) == [31.0, 32.0]
    assert list(figure.data[0].customdata) == [None, 0.4]
    assert list(figure.data[1].y) == [None, 0.4]


def test_identical_inputs_produce_equivalent_chart_data():
    event_time, readings, baseline, anomalies, forecast, target, peers, comparison = (
        _scenario_inputs()
    )
    first_readings = build_readings_baseline_anomaly_chart(
        readings,
        baseline=baseline,
        anomalies=anomalies,
        forecast=forecast,
        forecast_timestamp=event_time,
    )
    second_readings = build_readings_baseline_anomaly_chart(
        readings,
        baseline=baseline,
        anomalies=anomalies,
        forecast=forecast,
        forecast_timestamp=event_time,
    )
    assert first_readings.to_plotly_json()["data"] == second_readings.to_plotly_json()["data"]
    first_peers = build_peer_comparison_chart(target, peers, comparison=comparison)
    second_peers = build_peer_comparison_chart(target, peers, comparison=comparison)
    assert first_peers.to_plotly_json()["data"] == second_peers.to_plotly_json()["data"]