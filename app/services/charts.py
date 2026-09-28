from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import plotly.graph_objects as go

from app.tools.advanced import ForecastOutput
from app.tools.analytics import (
    AnomalyOutput,
    BaselineOutput,
    MeterSeries,
    PeerComparisonOutput,
    ReadingPoint,
)
from app.tools.weather import WeatherContextOutput


def build_readings_baseline_anomaly_chart(
    readings: Sequence[ReadingPoint],
    *,
    baseline: BaselineOutput | None = None,
    anomalies: AnomalyOutput | None = None,
    forecast: ForecastOutput | None = None,
    forecast_timestamp: datetime | None = None,
    title: str = "Meter readings and existing baseline evidence",
) -> go.Figure:
    """Plot supplied readings and analytics without recalculating them."""
    figure = go.Figure()
    ordered = sorted(readings, key=lambda item: item.timestamp)
    figure.add_trace(go.Scatter(
        x=[item.timestamp for item in ordered],
        y=[item.kwh for item in ordered],
        mode="lines+markers",
        name="Actual readings",
        connectgaps=False,
    ))

    if baseline is not None and baseline.status == "success":
        profiles: dict[tuple[int, int, int], list[float]] = defaultdict(list)
        for profile in baseline.profiles:
            key = (profile.weekday, profile.hour, int(profile.minute))
            profiles[key].append(profile.median_kwh)
        values = []
        for item in ordered:
            key = (
                item.timestamp.weekday(), item.timestamp.hour,
                0 if item.timestamp.minute < 30 else 30,
            )
            candidates = profiles.get(key, [])
            values.append(candidates[0] if len(candidates) == 1 else None)
        figure.add_trace(go.Scatter(
            x=[item.timestamp for item in ordered],
            y=values,
            mode="lines",
            name="Historical baseline median",
            connectgaps=False,
        ))

    if (
        forecast is not None
        and forecast.status == "answered"
        and forecast.expected_kwh is not None
        and forecast_timestamp is not None
    ):
        figure.add_trace(go.Scatter(
            x=[forecast_timestamp], y=[forecast.expected_kwh],
            mode="markers", name="Expected usage forecast",
        ))

    if anomalies is not None and anomalies.status == "anomalies_detected":
        figure.add_trace(go.Scatter(
            x=[event.timestamp for event in anomalies.events],
            y=[event.observed_kwh for event in anomalies.events],
            mode="markers",
            name="Existing anomaly events",
            marker={"size": 11, "symbol": "x"},
            customdata=[
                [event.anomaly_type, event.severity, event.expected_kwh]
                for event in anomalies.events
            ],
            hovertemplate=(
                "%{x}<br>Observed: %{y}<br>Type: %{customdata[0]}"
                "<br>Severity: %{customdata[1]}<br>Expected: %{customdata[2]}<extra></extra>"
            ),
        ))

    figure.update_layout(
        title=title, xaxis_title="Timestamp", yaxis_title="Energy (kWh)",
        legend_title="Supplied evidence",
    )
    return figure


def build_peer_comparison_chart(
    target: MeterSeries,
    peers: Sequence[MeterSeries],
    *,
    comparison: PeerComparisonOutput | None = None,
    title: str = "Target and selected peer readings",
) -> go.Figure:
    """Plot the target and caller-selected peer reading series."""
    figure = go.Figure()
    series = [(target, f"Target {target.meter_id}")]
    series.extend((peer, f"Peer {peer.meter_id}") for peer in peers)
    for meter, label in series:
        ordered = sorted(meter.readings, key=lambda item: item.timestamp)
        figure.add_trace(go.Scatter(
            x=[item.timestamp for item in ordered],
            y=[item.kwh for item in ordered],
            mode="lines+markers", name=label, connectgaps=False,
        ))

    annotations: list[dict[str, Any]] = []
    if comparison is not None:
        if comparison.status == "answered" and comparison.deviation_pct is not None:
            text = f"Supplied target deviation: {comparison.deviation_pct}%"
        elif comparison.status == "unknown":
            text = "Peer comparison unavailable"
        else:
            text = None
        if text is not None:
            annotations.append({
                "text": text, "xref": "paper", "yref": "paper",
                "x": 1, "y": 1, "showarrow": False, "xanchor": "right",
            })
    figure.update_layout(
        title=title, xaxis_title="Timestamp", yaxis_title="Energy (kWh)",
        legend_title="Meter", annotations=annotations,
    )
    return figure


def build_weather_context_chart(
    weather: WeatherContextOutput | None,
    *,
    title: str = "Supplied weather observations",
) -> go.Figure:
    """Plot only supplied weather observations, with an explicit empty state."""
    figure = go.Figure()
    if weather is None or weather.status != "answered" or not weather.observations:
        figure.update_layout(
            title=title, xaxis_title="Timestamp", yaxis_title="Temperature (°C)",
            annotations=[{
                "text": "Weather unavailable", "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5, "showarrow": False,
            }],
        )
        return figure

    ordered = sorted(weather.observations, key=lambda item: item.timestamp)
    timestamps = [item.timestamp for item in ordered]
    figure.add_trace(go.Scatter(
        x=timestamps,
        y=[item.temperature_c for item in ordered],
        mode="lines+markers",
        name="Temperature (°C)",
        customdata=[item.precipitation_mm for item in ordered],
        hovertemplate=(
            "%{x}<br>Temperature: %{y} °C"
            "<br>Precipitation: %{customdata} mm<extra></extra>"
        ),
    ))
    figure.add_trace(go.Scatter(
        x=timestamps,
        y=[item.precipitation_mm for item in ordered],
        mode="lines+markers", name="Precipitation (mm)", yaxis="y2",
        connectgaps=False,
    ))
    figure.update_layout(
        title=title, xaxis_title="Timestamp", yaxis={"title": "Temperature (°C)"},
        yaxis2={"title": "Precipitation (mm)", "overlaying": "y", "side": "right"},
        legend_title="Weather observation",
    )
    return figure
