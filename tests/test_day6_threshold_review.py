from datetime import UTC, datetime
from itertools import product
from math import inf, nextafter
from uuid import UUID

import pytest

from app.tools.analytics import (
    AnomalyInput,
    BaselineProfile,
    ReadingPoint,
    detect_anomaly,
)

CURRENT_IQR_MULTIPLIER = 1.50
CURRENT_MEDIAN_MULTIPLIER = 0.10
ABSOLUTE_FLOOR_KWH = 0.01

IQR_MULTIPLIERS = (1.25, 1.50, 1.75)
MEDIAN_MULTIPLIERS = (0.08, 0.10, 0.12)

REFERENCE_TIME = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

# Deterministic synthetic profiles. Each exercises a different active max() term.
PROFILE_SPECS = (
    ("iqr_dominant", 11.0, 10.0, 12.0, 2.0),
    ("median_relative_dominant", 10.0, 9.8, 10.2, 0.4),
    ("absolute_floor_dominant", 0.04, 0.038, 0.042, 0.004),
)


def _profile(spec) -> BaselineProfile:
    _, median, q1, q3, iqr = spec
    return BaselineProfile(
        weekday=REFERENCE_TIME.weekday(),
        hour=REFERENCE_TIME.hour,
        minute=REFERENCE_TIME.minute,
        sample_count=4,
        median_kwh=median,
        q1_kwh=q1,
        q3_kwh=q3,
        iqr_kwh=iqr,
    )


def _terms(profile: BaselineProfile, iqr_multiplier: float, median_multiplier: float):
    return (
        iqr_multiplier * profile.iqr_kwh,
        median_multiplier * abs(profile.median_kwh),
        ABSOLUTE_FLOOR_KWH,
    )


def _envelope(
    profile: BaselineProfile,
    iqr_multiplier: float,
    median_multiplier: float,
) -> tuple[float, float]:
    tolerance = max(*_terms(profile, iqr_multiplier, median_multiplier))
    return profile.q1_kwh - tolerance, profile.q3_kwh + tolerance


def _classify(
    reading: float,
    profile: BaselineProfile,
    iqr_multiplier: float,
    median_multiplier: float,
) -> str:
    lower, upper = _envelope(profile, iqr_multiplier, median_multiplier)
    if reading < lower:
        return "drop"
    if reading > upper:
        return "spike"
    return "normal"


def _development_samples():
    """Labels are defined against the current strict-boundary production policy."""
    samples = []
    for spec in PROFILE_SPECS:
        name = spec[0]
        profile = _profile(spec)
        lower, upper = _envelope(
            profile,
            CURRENT_IQR_MULTIPLIER,
            CURRENT_MEDIAN_MULTIPLIER,
        )
        samples.extend(
            (
                (name, profile, "normal", profile.median_kwh, "clear_normal"),
                (name, profile, "normal", lower, "exact_lower"),
                (name, profile, "drop", nextafter(lower, -inf), "just_below_lower"),
                (name, profile, "normal", upper, "exact_upper"),
                (name, profile, "spike", nextafter(upper, inf), "just_above_upper"),
            )
        )
    return samples


def _candidate_metrics(iqr_multiplier: float, median_multiplier: float) -> dict[str, int]:
    samples = _development_samples()
    normal_rows = [row for row in samples if row[2] == "normal"]
    drop_rows = [row for row in samples if row[2] == "drop"]
    spike_rows = [row for row in samples if row[2] == "spike"]
    return {
        "normal_samples_tested": len(normal_rows),
        "normal_false_positives": sum(
            _classify(row[3], row[1], iqr_multiplier, median_multiplier) != "normal"
            for row in normal_rows
        ),
        "drops_tested": len(drop_rows),
        "drop_detections": sum(
            _classify(row[3], row[1], iqr_multiplier, median_multiplier) == "drop"
            for row in drop_rows
        ),
        "spikes_tested": len(spike_rows),
        "spike_detections": sum(
            _classify(row[3], row[1], iqr_multiplier, median_multiplier) == "spike"
            for row in spike_rows
        ),
    }


def _production_classification(profile: BaselineProfile, reading: float) -> str:
    result = detect_anomaly(
        AnomalyInput(
            run_id=UUID(int=1),
            readings=[ReadingPoint(timestamp=REFERENCE_TIME, kwh=reading)],
            baseline_profiles=[profile],
        )
    )
    local_events = [event for event in result.events if event.anomaly_type in {"drop", "spike"}]
    return local_events[0].anomaly_type if local_events else "normal"


_PRODUCTION_CASES = [
    pytest.param(name, profile, label, value, case, id=f"{name}-{case}")
    for name, profile, label, value, case in _development_samples()
]

_EXPECTED_GRID = {
    (1.25, 0.08): (4, 3, 3),
    (1.25, 0.10): (2, 3, 3),
    (1.25, 0.12): (2, 2, 2),
    (1.50, 0.08): (2, 3, 3),
    (1.50, 0.10): (0, 3, 3),
    (1.50, 0.12): (0, 2, 2),
    (1.75, 0.08): (2, 2, 2),
    (1.75, 0.10): (0, 2, 2),
    (1.75, 0.12): (0, 1, 1),
}


def test_development_profiles_exercise_each_current_max_term():
    for name, *profile_values in PROFILE_SPECS:
        profile = _profile((name, *profile_values))
        iqr_term, median_term, floor_term = _terms(
            profile,
            CURRENT_IQR_MULTIPLIER,
            CURRENT_MEDIAN_MULTIPLIER,
        )
        if name == "iqr_dominant":
            assert iqr_term > median_term and iqr_term > floor_term
        elif name == "median_relative_dominant":
            assert median_term > iqr_term and median_term > floor_term
        else:
            assert floor_term > iqr_term and floor_term > median_term


@pytest.mark.parametrize(
    ("name", "profile", "expected", "reading", "case"),
    _PRODUCTION_CASES,
)
def test_current_production_detector_matches_development_boundary_labels(
    name,
    profile,
    expected,
    reading,
    case,
):
    assert _production_classification(profile, reading) == expected


@pytest.mark.parametrize(
    ("iqr_multiplier", "median_multiplier", "normal_fp", "drops_detected", "spikes_detected"),
    [
        (iqr, median, *expected)
        for (iqr, median), expected in _EXPECTED_GRID.items()
    ],
    ids=[
        f"iqr-{iqr}-median-{median}"
        for iqr, median in _EXPECTED_GRID
    ],
)
def test_candidate_grid_results_are_reproducible(
    iqr_multiplier,
    median_multiplier,
    normal_fp,
    drops_detected,
    spikes_detected,
):
    metrics = _candidate_metrics(iqr_multiplier, median_multiplier)
    assert metrics == {
        "normal_samples_tested": 9,
        "normal_false_positives": normal_fp,
        "drops_tested": 3,
        "drop_detections": drops_detected,
        "spikes_tested": 3,
        "spike_detections": spikes_detected,
    }


def test_candidate_grid_contains_exactly_nine_combinations():
    assert len(tuple(product(IQR_MULTIPLIERS, MEDIAN_MULTIPLIERS))) == 9
    assert set(_EXPECTED_GRID) == set(product(IQR_MULTIPLIERS, MEDIAN_MULTIPLIERS))
