"""Pure, deterministic Day 3/4 analytics.

The functions in this module do not read the database and never inspect evaluation
labels.  Database adapters build these typed inputs and the tool registry records the
result, latency, and any controlled failure.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import sqrt
from statistics import median
from typing import Literal

from pydantic import Field

from app.contracts.common import EvidenceReference
from app.contracts.tool import ToolInput, ToolOutput


class ReadingPoint(ToolOutput):
    """A normalized meter reading used by deterministic tools."""

    timestamp: datetime
    kwh: float | None = None
    quality_flag: str = "valid"
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class MeterSeries(ToolOutput):
    meter_id: str
    customer_segment: str | None = None
    has_solar: bool | None = None
    has_ev: bool | None = None
    readings: list[ReadingPoint]
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class QualityInput(ToolInput):
    readings: list[ReadingPoint]
    expected_interval_minutes: int = Field(default=30, ge=1, le=1440)
    flatline_min_intervals: int = Field(default=3, ge=2, le=48)
    source: str = "readings"


class QualityMetrics(ToolOutput):
    total_readings: int
    missing_values: int
    negative_values: int
    zero_values: int
    duplicate_timestamps: int
    missing_intervals: int
    max_flatline_run: int
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class QualityOutput(ToolOutput):
    status: Literal["valid", "warning", "critical"]
    quality_score: float = Field(ge=0, le=100)
    reliable: bool
    metrics: QualityMetrics


def _evidence(source: str, kind: str, observed_at: datetime | None, **metadata: object):
    return EvidenceReference(
        source=source,
        kind=kind,
        observed_at=observed_at,
        reliability=1.0,
        metadata=metadata,
    )


def validate_reading_quality(data: QualityInput) -> QualityOutput:
    readings = sorted(data.readings, key=lambda item: item.timestamp)
    total = len(readings)
    if total == 0:
        return QualityOutput(
            status="critical",
            quality_score=0,
            reliable=False,
            metrics=QualityMetrics(
                total_readings=0,
                missing_values=0,
                negative_values=0,
                zero_values=0,
                duplicate_timestamps=0,
                missing_intervals=0,
                max_flatline_run=0,
            ),
            evidence=[_evidence(data.source, "data_quality", None, empty_window=True)],
            warnings=["No readings were supplied."],
        )

    missing = sum(point.kwh is None for point in readings)
    negative = sum(point.kwh is not None and point.kwh < 0 for point in readings)
    zeros = sum(point.kwh == 0 for point in readings)
    duplicate_count = total - len({point.timestamp for point in readings})
    expected_seconds = data.expected_interval_minutes * 60
    missing_intervals = 0
    unique_times = sorted({point.timestamp for point in readings})
    for previous, current in zip(unique_times, unique_times[1:], strict=False):
        elapsed = (current - previous).total_seconds()
        if elapsed > expected_seconds:
            missing_intervals += max(0, round(elapsed / expected_seconds) - 1)

    max_run = 0
    run = 0
    previous_value: float | None = None
    for point in readings:
        if point.kwh is not None and point.kwh == previous_value:
            run += 1
        else:
            run = 1 if point.kwh is not None else 0
        max_run = max(max_run, run)
        previous_value = point.kwh

    denominator = max(total, 1)
    penalty = (
        missing * 2
        + negative * 5
        + duplicate_count * 2
        + missing_intervals * 2
        + (max_run if max_run >= data.flatline_min_intervals else 0)
    )
    score = round(max(0.0, 100.0 - (penalty / denominator * 100)), 2)
    critical = negative > 0 or missing > total / 2
    warning = (
        any((missing, duplicate_count, missing_intervals))
        or max_run >= data.flatline_min_intervals
    )
    status: Literal["valid", "warning", "critical"] = (
        "critical" if critical else "warning" if warning else "valid"
    )
    last_time = readings[-1].timestamp
    metrics = QualityMetrics(
        total_readings=total,
        missing_values=missing,
        negative_values=negative,
        zero_values=zeros,
        duplicate_timestamps=duplicate_count,
        missing_intervals=missing_intervals,
        max_flatline_run=max_run,
    )
    return QualityOutput(
        status=status,
        quality_score=score,
        reliable=not critical and score >= 70,
        metrics=metrics,
        evidence=[_evidence(data.source, "data_quality", last_time, **metrics.model_dump())],
        warnings=[] if status == "valid" else ["Reading-quality issues reduce confidence."],
    )


class BaselineInput(ToolInput):
    readings: list[ReadingPoint]
    cutoff: datetime | None = None
    minimum_samples_per_slot: int = Field(default=3, ge=2, le=100)
    source: str = "readings"


class BaselineProfile(ToolOutput):
    weekday: int = Field(ge=0, le=6)
    hour: int = Field(ge=0, le=23)
    minute: Literal[0, 30]
    sample_count: int
    median_kwh: float
    q1_kwh: float
    q3_kwh: float
    iqr_kwh: float
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class BaselineOutput(ToolOutput):
    status: Literal["success", "insufficient_history"]
    method: str = "weekday/half-hour median and IQR"
    profiles: list[BaselineProfile]
    excluded_future_readings: int = 0


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def calculate_baseline(data: BaselineInput) -> BaselineOutput:
    grouped: dict[tuple[int, int, int], list[float]] = defaultdict(list)
    excluded = 0
    included_times: list[datetime] = []
    for point in data.readings:
        if data.cutoff is not None and point.timestamp >= data.cutoff:
            excluded += 1
            continue
        if point.kwh is None or point.kwh < 0:
            continue
        minute = 0 if point.timestamp.minute < 30 else 30
        grouped[(point.timestamp.weekday(), point.timestamp.hour, minute)].append(point.kwh)
        included_times.append(point.timestamp)

    profiles = []
    for (weekday, hour, minute), values in sorted(grouped.items()):
        if len(values) < data.minimum_samples_per_slot:
            continue
        q1 = _percentile(values, 0.25)
        q3 = _percentile(values, 0.75)
        profiles.append(
            BaselineProfile(
                weekday=weekday,
                hour=hour,
                minute=minute,
                sample_count=len(values),
                median_kwh=round(median(values), 6),
                q1_kwh=round(q1, 6),
                q3_kwh=round(q3, 6),
                iqr_kwh=round(q3 - q1, 6),
            )
        )
    status: Literal["success", "insufficient_history"] = (
        "success" if profiles else "insufficient_history"
    )
    observed_at = max(included_times) if included_times else None
    return BaselineOutput(
        status=status,
        profiles=profiles,
        excluded_future_readings=excluded,
        evidence=[
            _evidence(
                data.source,
                "baseline",
                observed_at,
                profile_count=len(profiles),
                excluded_future_readings=excluded,
            )
        ],
        warnings=[] if profiles else ["No half-hour slot has enough historical samples."],
    )


class AnomalyInput(ToolInput):
    readings: list[ReadingPoint]
    baseline_profiles: list[BaselineProfile]
    expected_interval_minutes: int = Field(default=30, ge=1, le=1440)
    flatline_min_intervals: int = Field(default=3, ge=2, le=48)
    source: str = "readings"


class AnomalyEvent(ToolOutput):
    timestamp: datetime
    anomaly_type: Literal["drop", "spike", "gap", "zero_period", "flatline"]
    observed_kwh: float | None
    expected_kwh: float | None
    severity: float = Field(ge=0, le=100)
    components: dict[str, float | int | str]
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class AnomalyOutput(ToolOutput):
    status: Literal["normal", "anomalies_detected", "insufficient_baseline"]
    events: list[AnomalyEvent]
    overall_severity: float = Field(ge=0, le=100)


def _slot(timestamp: datetime) -> tuple[int, int, int]:
    return timestamp.weekday(), timestamp.hour, 0 if timestamp.minute < 30 else 30


def detect_anomaly(data: AnomalyInput) -> AnomalyOutput:
    profiles = {
        (item.weekday, item.hour, int(item.minute)): item for item in data.baseline_profiles
    }
    if not profiles:
        return AnomalyOutput(
            status="insufficient_baseline",
            events=[],
            overall_severity=0,
            warnings=["Anomaly detection requires a valid baseline."],
        )
    readings = sorted(data.readings, key=lambda item: item.timestamp)
    events: list[AnomalyEvent] = []
    expected_seconds = data.expected_interval_minutes * 60
    for previous, current in zip(readings, readings[1:], strict=False):
        elapsed = (current.timestamp - previous.timestamp).total_seconds()
        if elapsed > expected_seconds * 1.5:
            missing = max(1, round(elapsed / expected_seconds) - 1)
            events.append(
                AnomalyEvent(
                    timestamp=previous.timestamp,
                    anomaly_type="gap",
                    observed_kwh=None,
                    expected_kwh=None,
                    severity=min(100, 55 + missing * 5),
                    components={"missing_intervals": missing},
                )
            )

    same_run = 0
    previous_value: float | None = None
    for point in readings:
        profile = profiles.get(_slot(point.timestamp))
        if point.kwh is None:
            events.append(
                AnomalyEvent(
                    timestamp=point.timestamp,
                    anomaly_type="gap",
                    observed_kwh=None,
                    expected_kwh=profile.median_kwh if profile else None,
                    severity=80,
                    components={"reason": "missing_value"},
                )
            )
            same_run = 0
            previous_value = None
            continue
        same_run = same_run + 1 if point.kwh == previous_value else 1
        previous_value = point.kwh
        if same_run == data.flatline_min_intervals:
            events.append(
                AnomalyEvent(
                    timestamp=point.timestamp,
                    anomaly_type="flatline",
                    observed_kwh=point.kwh,
                    expected_kwh=profile.median_kwh if profile else None,
                    severity=50,
                    components={"consecutive_intervals": same_run},
                )
            )
        if profile is None:
            continue
        expected = profile.median_kwh
        if point.kwh == 0 and expected > 0:
            events.append(
                AnomalyEvent(
                    timestamp=point.timestamp,
                    anomaly_type="zero_period",
                    observed_kwh=0,
                    expected_kwh=expected,
                    severity=100,
                    components={"deviation_pct": 100.0},
                )
            )
            continue
        tolerance = max(profile.iqr_kwh * 1.5, abs(expected) * 0.10, 0.01)
        lower = profile.q1_kwh - tolerance
        upper = profile.q3_kwh + tolerance
        if point.kwh < lower:
            deviation = (expected - point.kwh) / expected if expected > 0 else 1.0
            events.append(
                AnomalyEvent(
                    timestamp=point.timestamp,
                    anomaly_type="drop",
                    observed_kwh=point.kwh,
                    expected_kwh=expected,
                    severity=round(min(100, max(0, deviation * 100)), 2),
                    components={"deviation_pct": round(deviation * 100, 2), "lower_bound": lower},
                )
            )
        elif point.kwh > upper:
            deviation = (point.kwh - expected) / expected if expected > 0 else 1.0
            events.append(
                AnomalyEvent(
                    timestamp=point.timestamp,
                    anomaly_type="spike",
                    observed_kwh=point.kwh,
                    expected_kwh=expected,
                    severity=round(min(100, max(0, deviation * 100)), 2),
                    components={"deviation_pct": round(deviation * 100, 2), "upper_bound": upper},
                )
            )
    for event in events:
        event.evidence.append(
            _evidence(
                data.source,
                "anomaly",
                event.timestamp,
                anomaly_type=event.anomaly_type,
                severity=event.severity,
            )
        )
    return AnomalyOutput(
        status="anomalies_detected" if events else "normal",
        events=events,
        overall_severity=max((event.severity for event in events), default=0),
        evidence=[item for event in events for item in event.evidence],
    )


class PeerSelectionInput(ToolInput):
    target: MeterSeries
    candidates: list[MeterSeries]
    event_time: datetime
    minimum_overlap: int = Field(default=12, ge=3, le=10_000)
    minimum_similarity: float = Field(default=0.5, ge=-1, le=1)
    maximum_peers: int = Field(default=5, ge=1, le=50)


class PeerCandidate(ToolOutput):
    meter_id: str
    similarity: float = Field(ge=-1, le=1)
    overlap: int
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class PeerSelectionOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    peers: list[PeerCandidate]


def _series_before(series: MeterSeries, cutoff: datetime) -> dict[datetime, float]:
    return {
        point.timestamp: point.kwh
        for point in series.readings
        if point.timestamp < cutoff and point.kwh is not None and point.kwh >= 0
    }


def _correlation(left: list[float], right: list[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_scale = sqrt(sum((item - left_mean) ** 2 for item in left))
    right_scale = sqrt(sum((item - right_mean) ** 2 for item in right))
    if left_scale == 0 or right_scale == 0:
        return 1.0 if left == right else 0.0
    return numerator / (left_scale * right_scale)


def select_dynamic_peers(data: PeerSelectionInput) -> PeerSelectionOutput:
    target_values = _series_before(data.target, data.event_time)
    peers = []
    for candidate in data.candidates:
        if candidate.meter_id == data.target.meter_id:
            continue
        if (
            data.target.customer_segment
            and candidate.customer_segment != data.target.customer_segment
        ):
            continue
        if data.target.has_solar is not None and candidate.has_solar != data.target.has_solar:
            continue
        if data.target.has_ev is not None and candidate.has_ev != data.target.has_ev:
            continue
        candidate_values = _series_before(candidate, data.event_time)
        overlap = sorted(set(target_values) & set(candidate_values))
        if len(overlap) < data.minimum_overlap:
            continue
        similarity = _correlation(
            [target_values[time] for time in overlap],
            [candidate_values[time] for time in overlap],
        )
        if similarity >= data.minimum_similarity:
            peers.append(
                PeerCandidate(
                    meter_id=candidate.meter_id,
                    similarity=round(similarity, 6),
                    overlap=len(overlap),
                )
            )
    peers.sort(key=lambda item: (-item.similarity, item.meter_id))
    peers = peers[: data.maximum_peers]
    status: Literal["answered", "unknown"] = "answered" if peers else "unknown"
    return PeerSelectionOutput(
        status=status,
        peers=peers,
        evidence=[
            _evidence(
                "meter_readings",
                "peer_selection",
                data.event_time,
                peer_ids=[item.meter_id for item in peers],
                pre_event_only=True,
            )
        ],
        warnings=[] if peers else ["No candidate met metadata, overlap, and similarity rules."],
    )


class PeerComparisonInput(ToolInput):
    target: MeterSeries
    peers: list[MeterSeries]
    event_time: datetime


class PeerComparisonOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    target_kwh: float | None = None
    peer_count: int = 0
    peer_median_kwh: float | None = None
    deviation_pct: float | None = None
    percentile: float | None = None


def _value_at(series: MeterSeries, timestamp: datetime) -> float | None:
    values = [point.kwh for point in series.readings if point.timestamp == timestamp]
    if len(values) != 1:
        return None
    return values[0]


def compare_with_peers(data: PeerComparisonInput) -> PeerComparisonOutput:
    target = _value_at(data.target, data.event_time)
    peer_values = [
        value
        for peer in data.peers
        if (value := _value_at(peer, data.event_time)) is not None
    ]
    if target is None or not peer_values:
        return PeerComparisonOutput(
            status="unknown",
            warnings=["Target or peer readings are unavailable or duplicated at the event time."],
        )
    peer_median = median(peer_values)
    deviation = ((target - peer_median) / peer_median * 100) if peer_median else None
    percentile = sum(value <= target for value in peer_values) / len(peer_values) * 100
    return PeerComparisonOutput(
        status="answered",
        target_kwh=target,
        peer_count=len(peer_values),
        peer_median_kwh=round(peer_median, 6),
        deviation_pct=round(deviation, 2) if deviation is not None else None,
        percentile=round(percentile, 2),
        evidence=[
            _evidence(
                "meter_readings",
                "peer_comparison",
                data.event_time,
                peer_count=len(peer_values),
            )
        ],
    )


class AssetNode(ToolOutput):
    asset_id: str
    asset_type: Literal["substation", "feeder", "transformer"]
    parent_id: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class MeterConnection(ToolOutput):
    meter_id: str
    transformer_id: str
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class ConnectedAssetsInput(ToolInput):
    target_meter_id: str
    assets: list[AssetNode]
    meters: list[MeterConnection]


class ConnectedAssetsOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    transformer_id: str | None = None
    feeder_id: str | None = None
    substation_id: str | None = None
    connected_meter_ids: list[str] = Field(default_factory=list)


def get_connected_assets(data: ConnectedAssetsInput) -> ConnectedAssetsOutput:
    meter_map = {item.meter_id: item.transformer_id for item in data.meters}
    transformer_id = meter_map.get(data.target_meter_id)
    if transformer_id is None:
        return ConnectedAssetsOutput(
            status="unknown", warnings=["Target meter is not connected to a transformer."]
        )
    asset_map = {item.asset_id: item for item in data.assets}
    transformer = asset_map.get(transformer_id)
    feeder_id = transformer.parent_id if transformer else None
    feeder = asset_map.get(feeder_id) if feeder_id else None
    substation_id = feeder.parent_id if feeder else None
    connected = sorted(
        meter_id
        for meter_id, linked_transformer in meter_map.items()
        if linked_transformer == transformer_id and meter_id != data.target_meter_id
    )
    return ConnectedAssetsOutput(
        status="answered",
        transformer_id=transformer_id,
        feeder_id=feeder_id,
        substation_id=substation_id,
        connected_meter_ids=connected,
        evidence=[
            _evidence(
                "topology",
                "connected_assets",
                None,
                transformer_id=transformer_id,
                feeder_id=feeder_id,
                substation_id=substation_id,
                connected_meter_ids=connected,
            )
        ],
        warnings=[] if transformer and feeder else ["Topology hierarchy is incomplete."],
    )


class SharedIncidentInput(ToolInput):
    connected_series: list[MeterSeries]
    event_time: datetime
    lookback_periods: int = Field(default=6, ge=3, le=100)
    drop_threshold: float = Field(default=0.40, ge=0, le=1)
    shared_threshold: float = Field(default=0.50, ge=0, le=1)
    minimum_answered_meters: int = Field(default=2, ge=1, le=1000)


class SharedMeterResult(ToolOutput):
    meter_id: str
    status: Literal["answered", "unknown"]
    current_kwh: float | None = None
    baseline_kwh: float | None = None
    drop_fraction: float | None = None
    affected: bool | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class SharedIncidentOutput(ToolOutput):
    status: Literal["answered", "unknown"]
    incident_type: Literal["local", "shared", "unknown"]
    confidence: float = Field(ge=0, le=1)
    affected_count: int = 0
    answered_count: int = 0
    affected_fraction: float | None = None
    meter_results: list[SharedMeterResult] = Field(default_factory=list)


def detect_shared_incident(data: SharedIncidentInput) -> SharedIncidentOutput:
    results = []
    for series in data.connected_series:
        current = _value_at(series, data.event_time)
        historical = sorted(
            (
                point
                for point in series.readings
                if point.timestamp < data.event_time and point.kwh is not None and point.kwh >= 0
            ),
            key=lambda item: item.timestamp,
        )[-data.lookback_periods :]
        if current is None or len(historical) < data.lookback_periods:
            results.append(SharedMeterResult(meter_id=series.meter_id, status="unknown"))
            continue
        baseline = median([point.kwh for point in historical if point.kwh is not None])
        if baseline <= 0:
            results.append(SharedMeterResult(meter_id=series.meter_id, status="unknown"))
            continue
        drop = (baseline - current) / baseline
        results.append(
            SharedMeterResult(
                meter_id=series.meter_id,
                status="answered",
                current_kwh=current,
                baseline_kwh=round(baseline, 6),
                drop_fraction=round(drop, 6),
                affected=drop >= data.drop_threshold,
            )
        )
    answered = [item for item in results if item.status == "answered"]
    if len(answered) < data.minimum_answered_meters:
        return SharedIncidentOutput(
            status="unknown",
            incident_type="unknown",
            confidence=0,
            answered_count=len(answered),
            meter_results=results,
            warnings=["Too few connected meters have current and historical evidence."],
        )
    affected_count = sum(item.affected is True for item in answered)
    fraction = affected_count / len(answered)
    shared = fraction >= data.shared_threshold
    distance = abs(fraction - data.shared_threshold) / max(
        data.shared_threshold, 1 - data.shared_threshold
    )
    coverage = len(answered) / max(len(data.connected_series), 1)
    confidence = min(1.0, 0.5 + distance * 0.3 + coverage * 0.2)
    return SharedIncidentOutput(
        status="answered",
        incident_type="shared" if shared else "local",
        confidence=round(confidence, 2),
        affected_count=affected_count,
        answered_count=len(answered),
        affected_fraction=round(fraction, 4),
        meter_results=results,
        evidence=[
            _evidence(
                "meter_readings",
                "shared_incident",
                data.event_time,
                affected_count=affected_count,
                answered_count=len(answered),
                affected_fraction=round(fraction, 4),
            )
        ],
    )
