from datetime import UTC, datetime, timedelta, timezone
from math import isfinite
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.tools.analytics import (
    AnomalyInput,
    AnomalyOutput,
    BaselineProfile,
    MeterSeries,
    PeerComparisonOutput,
    ReadingPoint,
    detect_anomaly,
)
from app.tools.verification import (
    ObservationEvidence,
    ObservationWindowResult,
    PeerAgreementReference,
    PeerAgreementResult,
    ReturnToBaselineResult,
    VerificationSummaryResult,
    build_peer_agreement_reference,
    check_observation_window,
    evaluate_peer_agreement,
    evaluate_return_to_baseline,
    summarize_verification,
)


def _reference() -> PeerAgreementReference:
    return PeerAgreementReference(
        status="answered",
        target_meter_id="M1",
        peer_ids=("M2", "M3"),
        cutoff=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        support_count=30,
        lower=-8.0,
        upper=-4.0,
    )


def test_peer_agreement_passes_inside_historical_range():
    current = PeerComparisonOutput(
        status="answered",
        target_kwh=10.0,
        peer_count=2,
        peer_median_kwh=10.6,
        deviation_pct=-5.66,
    )

    result = evaluate_peer_agreement(
        reference=_reference(),
        current=current,
        target_meter_id="M1",
        current_peer_ids=("M2", "M3"),
        comparison_timestamp=datetime(2026, 9, 20, 12, 30, tzinfo=UTC),
    )

    assert result.status == "pass"
    assert result.current_deviation_pct == -5.66


def test_peer_agreement_fails_outside_historical_range():
    current = PeerComparisonOutput(
        status="answered",
        target_kwh=3.0,
        peer_count=2,
        peer_median_kwh=10.6,
        deviation_pct=-71.70,
    )

    result = evaluate_peer_agreement(
        reference=_reference(),
        current=current,
        target_meter_id="M1",
        current_peer_ids=("M2", "M3"),
        comparison_timestamp=datetime(2026, 9, 20, 12, 30, tzinfo=UTC),
    )

    assert result.status == "fail"
    assert result.current_deviation_pct == -71.70


def test_peer_agreement_is_unknown_when_peer_median_is_zero():
    current = PeerComparisonOutput(
        status="answered",
        target_kwh=0.0,
        peer_count=2,
        peer_median_kwh=0.0,
        deviation_pct=None,
    )

    result = evaluate_peer_agreement(
        reference=_reference(),
        current=current,
        target_meter_id="M1",
        current_peer_ids=("M2", "M3"),
        comparison_timestamp=datetime(2026, 9, 20, 12, 30, tzinfo=UTC),
    )

    assert result.status == "unknown"
    assert result.reason == "Current peer median is unavailable or zero."

@pytest.fixture
def valid_inputs():
    return {
        "reference": _reference(),
        "current": PeerComparisonOutput(
            status="answered",
            target_kwh=10.0,
            peer_count=2,
            peer_median_kwh=10.6,
            deviation_pct=-5.66,
        ),
        "target_meter_id": "M1",
        "current_peer_ids": ("M2", "M3"),
        "comparison_timestamp": datetime(2026, 9, 20, 12, 30, tzinfo=UTC),
    }


def _assert_unknown(result):
    assert result.status == "unknown"
    assert result.current_deviation_pct is None
    assert result.reason


def test_missing_reference_is_unknown(valid_inputs):
    valid_inputs["reference"] = None
    _assert_unknown(evaluate_peer_agreement(**valid_inputs))


@pytest.mark.parametrize(
    "updates",
    [
        pytest.param({"status": "unknown"}, id="unknown-reference"),
        pytest.param({"support_count": 11}, id="insufficient-support"),
        pytest.param({"lower": None}, id="missing-lower"),
        pytest.param({"upper": None}, id="missing-upper"),
        pytest.param({"lower": float("nan")}, id="nan-lower"),
        pytest.param({"upper": float("inf")}, id="infinite-upper"),
        pytest.param({"lower": -3.0, "upper": -4.0}, id="reversed-bounds"),
        pytest.param({"peer_ids": ()}, id="empty-reference-peers"),
        pytest.param({"peer_ids": ("M2", "M2")}, id="duplicate-reference-peers"),
        pytest.param({"peer_ids": ("M1", "M3")}, id="reference-contains-target"),
        pytest.param({"target_meter_id": "M9"}, id="target-mismatch"),
    ],
)
def test_invalid_reference_is_unknown(valid_inputs, updates):
    valid_inputs["reference"] = PeerAgreementReference(
        **(valid_inputs["reference"].model_dump() | updates)
    )
    _assert_unknown(evaluate_peer_agreement(**valid_inputs))


def test_minimum_support_evaluates(valid_inputs):
    valid_inputs["reference"].support_count = 12
    result = evaluate_peer_agreement(**valid_inputs)
    assert result.status == "pass"
    assert result.support_count == 12
    assert result.current_deviation_pct == -5.66


@pytest.mark.parametrize("identifier", ["", " \t "])
@pytest.mark.parametrize("location", ["reference", "current", "both"])
def test_blank_target_identifiers_are_unknown(valid_inputs, identifier, location):
    if location in ("reference", "both"):
        valid_inputs["reference"].target_meter_id = identifier
    if location in ("current", "both"):
        valid_inputs["target_meter_id"] = identifier
    _assert_unknown(evaluate_peer_agreement(**valid_inputs))


@pytest.mark.parametrize("identifier", ["", " \t "])
@pytest.mark.parametrize("location", ["reference", "current", "both"])
def test_blank_peer_identifiers_are_unknown(valid_inputs, identifier, location):
    # Matching invalid IDs must not pass merely because membership matches.
    if location in ("reference", "both"):
        valid_inputs["reference"].peer_ids = (identifier, "M3")
    if location in ("current", "both"):
        valid_inputs["current_peer_ids"] = (identifier, "M3")
    _assert_unknown(evaluate_peer_agreement(**valid_inputs))


@pytest.mark.parametrize(
    "peer_ids",
    [
        pytest.param((), id="empty"),
        pytest.param(("M2", "M2"), id="duplicate"),
        pytest.param(("M1", "M3"), id="contains-target"),
        pytest.param(("M2", "M4"), id="changed-membership"),
    ],
)
def test_invalid_current_membership_is_unknown(valid_inputs, peer_ids):
    valid_inputs["current_peer_ids"] = peer_ids
    valid_inputs["current"].peer_count = len(peer_ids)
    _assert_unknown(evaluate_peer_agreement(**valid_inputs))


def test_reordered_membership_evaluates_normally(valid_inputs):
    expected = evaluate_peer_agreement(**valid_inputs)
    valid_inputs["current_peer_ids"] = ("M3", "M2")
    assert evaluate_peer_agreement(**valid_inputs) == expected
    assert expected.status == "pass"


@pytest.mark.parametrize("peer_count", [0, 1, 3])
def test_peer_count_mismatch_is_unknown(valid_inputs, peer_count):
    valid_inputs["current"].peer_count = peer_count
    _assert_unknown(evaluate_peer_agreement(**valid_inputs))


@pytest.mark.parametrize(
    "timestamp, expected_status",
    [
        pytest.param(datetime(2026, 9, 20, 12, 30), "unknown", id="naive"),
        pytest.param(
            datetime(2026, 9, 20, 11, 59, 59, tzinfo=UTC), "unknown", id="before-cutoff"
        ),
        pytest.param(datetime(2026, 9, 20, 12, 0, tzinfo=UTC), "pass", id="at-cutoff"),
    ],
)
def test_comparison_timestamp(valid_inputs, timestamp, expected_status):
    valid_inputs["comparison_timestamp"] = timestamp
    result = evaluate_peer_agreement(**valid_inputs)
    assert result.status == expected_status
    if expected_status == "unknown":
        _assert_unknown(result)
    else:
        assert result.current_deviation_pct == -5.66


def test_reference_rejects_naive_cutoff():
    fields = _reference().model_dump() | {"cutoff": datetime(2026, 9, 20, 12, 0)}
    with pytest.raises(ValidationError) as exc_info:
        PeerAgreementReference(**fields)
    assert any(
        error["loc"] == ("cutoff",) and error["type"] == "timezone_aware"
        for error in exc_info.value.errors()
    )


def test_unanswered_current_comparison_is_unknown(valid_inputs):
    valid_inputs["current"].status = "unknown"
    _assert_unknown(evaluate_peer_agreement(**valid_inputs))


@pytest.mark.parametrize("median", [None, float("nan"), float("inf"), -float("inf"), 0.0])
def test_invalid_current_median_is_unknown(valid_inputs, median):
    valid_inputs["current"].peer_median_kwh = median
    _assert_unknown(evaluate_peer_agreement(**valid_inputs))


@pytest.mark.parametrize("deviation", [None, float("nan"), float("inf"), -float("inf")])
def test_invalid_current_deviation_is_unknown(valid_inputs, deviation):
    valid_inputs["current"].deviation_pct = deviation
    _assert_unknown(evaluate_peer_agreement(**valid_inputs))


@pytest.mark.parametrize("median", [None, float("nan"), float("inf"), -float("inf"), 0.0])
@pytest.mark.parametrize("deviation", [float("nan"), float("inf"), -float("inf")])
def test_invalid_median_does_not_leak_nonfinite_deviation(valid_inputs, median, deviation):
    valid_inputs["current"].peer_median_kwh = median
    valid_inputs["current"].deviation_pct = deviation
    result = evaluate_peer_agreement(**valid_inputs)
    assert result.status == "unknown"
    assert result.current_deviation_pct is None or isfinite(result.current_deviation_pct)
    assert result.current_deviation_pct != 0.0


@pytest.mark.parametrize(
    "lower, upper, deviation, expected_status",
    [
        pytest.param(-8.0, -4.0, -8.0, "pass", id="exact-lower"),
        pytest.param(-8.0, -4.0, -4.0, "pass", id="exact-upper"),
        pytest.param(-8.0, -4.0, -8.01, "fail", id="below-lower"),
        pytest.param(-8.0, -4.0, -3.99, "fail", id="above-upper"),
        pytest.param(-6.0, -6.0, -6.0, "pass", id="equal-bounds-exact"),
        pytest.param(-6.0, -6.0, -6.01, "fail", id="equal-bounds-below"),
        pytest.param(-6.0, -6.0, -5.99, "fail", id="equal-bounds-above"),
        pytest.param(4.0, 8.0, 6.0, "pass", id="positive-range"),
        pytest.param(4.0, 8.0, -6.0, "fail", id="positive-range-negative-value"),
        pytest.param(-52.0, -48.0, -50.0, "pass", id="signed-negative-range"),
        pytest.param(-52.0, -48.0, 50.0, "fail", id="opposite-sign"),
    ],
)
def test_inclusive_signed_boundaries(valid_inputs, lower, upper, deviation, expected_status):
    valid_inputs["reference"].lower = lower
    valid_inputs["reference"].upper = upper
    valid_inputs["current"].deviation_pct = deviation
    result = evaluate_peer_agreement(**valid_inputs)
    assert result.status == expected_status
    assert result.current_deviation_pct == deviation
    assert result.lower == lower
    assert result.upper == upper


@pytest.fixture
def baseline_inputs():
    timestamp = datetime(2026, 9, 20, 12, 30, tzinfo=UTC)
    data = AnomalyInput(
        run_id=UUID(int=1),
        readings=[ReadingPoint(timestamp=timestamp, kwh=10.0)],
        baseline_profiles=[
            BaselineProfile(
                weekday=timestamp.weekday(), hour=12, minute=30,
                sample_count=4, median_kwh=10.0, q1_kwh=9.0, q3_kwh=11.0, iqr_kwh=2.0,
            )
        ],
    )
    return {
        "anomaly_input": data,
        "anomaly_result": detect_anomaly(data),
        "comparison_timestamp": timestamp,
        "data_reliable": True,
    }


def _assert_baseline_unknown(inputs):
    result = evaluate_return_to_baseline(**inputs)
    assert result.status == "unknown"
    assert result.reason


@pytest.mark.parametrize(
    "field, value",
    [("data_reliable", None), ("data_reliable", False),
     ("anomaly_input", None), ("anomaly_result", None)],
)
def test_baseline_unavailable_inputs(baseline_inputs, field, value):
    baseline_inputs[field] = value
    _assert_baseline_unknown(baseline_inputs)


def test_baseline_naive_comparison(baseline_inputs):
    baseline_inputs["comparison_timestamp"] = baseline_inputs["comparison_timestamp"].replace(
        tzinfo=None
    )
    _assert_baseline_unknown(baseline_inputs)


@pytest.mark.parametrize("case", ["empty", "different-time", "duplicate"])
def test_baseline_requires_exactly_one_current_reading(baseline_inputs, case):
    data = baseline_inputs["anomaly_input"]
    reading = data.readings[0]
    if case == "empty":
        data.readings = []
    elif case == "different-time":
        data.readings = [reading.model_copy(update={
            "timestamp": reading.timestamp - timedelta(minutes=30)
        })]
    else:
        data.readings = [reading, reading.model_copy()]
    assert baseline_inputs["anomaly_result"].status == "normal"
    _assert_baseline_unknown(baseline_inputs)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf")])
def test_baseline_invalid_reading_cannot_pass_normal_result(baseline_inputs, value):
    baseline_inputs["anomaly_input"].readings[0].kwh = value
    assert baseline_inputs["anomaly_result"].status == "normal"
    _assert_baseline_unknown(baseline_inputs)


@pytest.mark.parametrize("case", ["empty", "wrong-slot", "duplicate"])
def test_baseline_requires_unique_matching_profile(baseline_inputs, case):
    data = baseline_inputs["anomaly_input"]
    profile = data.baseline_profiles[0]
    if case == "empty":
        data.baseline_profiles = []
    elif case == "wrong-slot":
        profile.minute = 0
    else:
        data.baseline_profiles = [profile, profile.model_copy()]
    assert baseline_inputs["anomaly_result"].status == "normal"
    _assert_baseline_unknown(baseline_inputs)


@pytest.mark.parametrize("count", [0, -1])
def test_baseline_invalid_support(baseline_inputs, count):
    baseline_inputs["anomaly_input"].baseline_profiles[0].sample_count = count
    _assert_baseline_unknown(baseline_inputs)


@pytest.mark.parametrize("field", ["median_kwh", "q1_kwh", "q3_kwh", "iqr_kwh"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_baseline_nonfinite_statistics(baseline_inputs, field, value):
    setattr(baseline_inputs["anomaly_input"].baseline_profiles[0], field, value)
    _assert_baseline_unknown(baseline_inputs)


@pytest.mark.parametrize("field", ["median_kwh", "q1_kwh", "q3_kwh", "iqr_kwh"])
def test_baseline_contract_rejects_none_statistics(baseline_inputs, field):
    fields = baseline_inputs["anomaly_input"].baseline_profiles[0].model_dump()
    fields[field] = None
    with pytest.raises(ValidationError):
        BaselineProfile(**fields)


@pytest.mark.parametrize("updates", [{"q1_kwh": 12.0}, {"iqr_kwh": -1.0}])
def test_baseline_invalid_quartile_order_or_negative_iqr(baseline_inputs, updates):
    profile = baseline_inputs["anomaly_input"].baseline_profiles[0]
    baseline_inputs["anomaly_input"].baseline_profiles = [
        BaselineProfile(**(profile.model_dump() | updates))
    ]
    _assert_baseline_unknown(baseline_inputs)


@pytest.mark.parametrize("updates", [{"median_kwh": 20.0}, {"iqr_kwh": 100.0}])
def test_baseline_inconsistent_statistics_cannot_pass(baseline_inputs, updates):
    profile = baseline_inputs["anomaly_input"].baseline_profiles[0]
    baseline_inputs["anomaly_input"].baseline_profiles = [
        BaselineProfile(**(profile.model_dump() | updates))
    ]
    baseline_inputs["anomaly_result"] = detect_anomaly(baseline_inputs["anomaly_input"])
    _assert_baseline_unknown(baseline_inputs)


def test_baseline_insufficient_anomaly_evidence(baseline_inputs):
    baseline_inputs["anomaly_result"] = AnomalyOutput(
        status="insufficient_baseline", events=[], overall_severity=0,
    )
    _assert_baseline_unknown(baseline_inputs)


def test_baseline_valid_normal_passes_deterministically(baseline_inputs):
    first = evaluate_return_to_baseline(**baseline_inputs)
    second = evaluate_return_to_baseline(**baseline_inputs)
    assert first.status == "pass"
    assert first.reason
    assert first == second


@pytest.mark.parametrize("value, kind", [(3.0, "drop"), (17.0, "spike"), (0.0, "zero_period")])
def test_baseline_current_detector_anomaly_fails(baseline_inputs, value, kind):
    data = baseline_inputs["anomaly_input"]
    data.readings[0].kwh = value
    baseline_inputs["anomaly_result"] = detect_anomaly(data)
    assert kind in {event.anomaly_type for event in baseline_inputs["anomaly_result"].events}
    assert evaluate_return_to_baseline(**baseline_inputs).status == "fail"


@pytest.mark.parametrize("length", [3, 4, 5])
def test_baseline_current_or_ongoing_flatline_must_fail(baseline_inputs, length):
    data = baseline_inputs["anomaly_input"]
    now = baseline_inputs["comparison_timestamp"]
    data.readings = [
        ReadingPoint(timestamp=now - timedelta(minutes=30 * offset), kwh=10.0)
        for offset in reversed(range(length))
    ]
    baseline_inputs["anomaly_result"] = detect_anomaly(data)
    flatlines = [
        e for e in baseline_inputs["anomaly_result"].events if e.anomaly_type == "flatline"
    ]
    assert len(flatlines) == 1
    assert flatlines[0].timestamp == data.readings[2].timestamp
    assert evaluate_return_to_baseline(**baseline_inputs).status == "fail"


def test_baseline_current_gap_is_unknown(baseline_inputs):
    # Day 3 attaches an inter-reading gap to the preceding (current) observation.
    data = baseline_inputs["anomaly_input"]
    now = baseline_inputs["comparison_timestamp"]
    data.readings.append(ReadingPoint(timestamp=now + timedelta(minutes=90), kwh=11.0))
    baseline_inputs["anomaly_result"] = detect_anomaly(data)
    assert any(e.anomaly_type == "gap" and e.timestamp == now
               for e in baseline_inputs["anomaly_result"].events)
    _assert_baseline_unknown(baseline_inputs)


def test_baseline_unrelated_past_anomaly_does_not_fail_current(baseline_inputs):
    data = baseline_inputs["anomaly_input"]
    now = baseline_inputs["comparison_timestamp"]
    previous = now - timedelta(minutes=30)
    data.readings.insert(0, ReadingPoint(timestamp=previous, kwh=3.0))
    profile = data.baseline_profiles[0]
    data.baseline_profiles.append(profile.model_copy(update={"minute": 0}))
    baseline_inputs["anomaly_result"] = detect_anomaly(data)
    assert baseline_inputs["anomaly_result"].status == "anomalies_detected"
    assert all(e.timestamp == previous for e in baseline_inputs["anomaly_result"].events)
    assert evaluate_return_to_baseline(**baseline_inputs).status == "pass"


def test_baseline_equivalent_timestamp_preserves_reading_slot(baseline_inputs):
    baseline_inputs["comparison_timestamp"] = baseline_inputs["comparison_timestamp"].astimezone(
        timezone(timedelta(hours=3))
    )
    assert evaluate_return_to_baseline(**baseline_inputs).status == "pass"


def test_baseline_reuses_detector_half_hour_slot(baseline_inputs):
    data = baseline_inputs["anomaly_input"]
    timestamp = baseline_inputs["comparison_timestamp"] + timedelta(minutes=5)
    data.readings[0].timestamp = timestamp
    baseline_inputs["comparison_timestamp"] = timestamp
    baseline_inputs["anomaly_result"] = detect_anomaly(data)
    assert baseline_inputs["anomaly_result"].status == "normal"
    assert evaluate_return_to_baseline(**baseline_inputs).status == "pass"


@pytest.fixture
def window_inputs():
    reference = datetime(2026, 9, 20, 12, tzinfo=UTC)
    slot = reference + timedelta(minutes=30)
    return {
        "expected_timestamps": (slot,),
        "observations": (_window_observation(slot),),
        "reference_timestamp": reference,
        "as_of": slot,
    }


def _window_observation(timestamp, kwh=10.0, reliable=True):
    return ObservationEvidence(
        reading=ReadingPoint(timestamp=timestamp, kwh=kwh), data_reliable=reliable,
    )


def _assert_window(inputs, status, usable=0):
    result = check_observation_window(**inputs)
    assert result.status == status
    assert result.expected_count == len(inputs["expected_timestamps"])
    assert result.usable_count == usable
    assert result.reason
    return result


@pytest.mark.parametrize("count", [1, 2, 7])
@pytest.mark.parametrize("kwh", [0.0, 10.0])
def test_window_valid_explicit_schedule(window_inputs, count, kwh):
    slots = tuple(window_inputs["reference_timestamp"] + timedelta(minutes=30 * n)
                  for n in range(1, count + 1))
    window_inputs.update(
        expected_timestamps=slots,
        observations=tuple(_window_observation(t, kwh) for t in slots),
        as_of=slots[-1],
    )
    first = _assert_window(window_inputs, "ready", count)
    assert check_observation_window(**window_inputs) == first


def test_window_equivalent_offset_instants(window_inputs):
    zone = timezone(timedelta(hours=3))
    slot = window_inputs["expected_timestamps"][0]
    window_inputs["observations"] = (_window_observation(slot.astimezone(zone)),)
    window_inputs["reference_timestamp"] = window_inputs["reference_timestamp"].astimezone(zone)
    window_inputs["as_of"] = window_inputs["as_of"].astimezone(zone)
    _assert_window(window_inputs, "ready", 1)


@pytest.mark.parametrize(
    "case", ["empty", "duplicate", "duplicate-offset", "naive", "at-reference", "before-reference"]
)
def test_window_invalid_schedule(window_inputs, case):
    slot = window_inputs["expected_timestamps"][0]
    schedules = {
        "empty": (),
        "duplicate": (slot, slot),
        "duplicate-offset": (slot, slot.astimezone(timezone(timedelta(hours=3)))),
        "naive": (slot.replace(tzinfo=None),),
        "at-reference": (window_inputs["reference_timestamp"],),
        "before-reference": (window_inputs["reference_timestamp"] - timedelta(seconds=1),),
    }
    window_inputs["expected_timestamps"] = schedules[case]
    _assert_window(window_inputs, "unknown")


@pytest.mark.parametrize("field", ["reference_timestamp", "as_of"])
def test_window_naive_boundary(window_inputs, field):
    window_inputs[field] = window_inputs[field].replace(tzinfo=None)
    _assert_window(window_inputs, "unknown")


def test_window_as_of_before_reference(window_inputs):
    window_inputs["as_of"] = window_inputs["reference_timestamp"] - timedelta(seconds=1)
    _assert_window(window_inputs, "unknown")


@pytest.mark.parametrize("case", ["missing", "duplicate", "duplicate-offset", "off-slot"])
def test_window_requires_exactly_one_observation(window_inputs, case):
    slot = window_inputs["expected_timestamps"][0]
    observations = {
        "missing": (),
        "duplicate": (_window_observation(slot), _window_observation(slot)),
        "duplicate-offset": (
            _window_observation(slot),
            _window_observation(slot.astimezone(timezone(timedelta(hours=3)))),
        ),
        "off-slot": (_window_observation(slot + timedelta(seconds=1)),),
    }
    window_inputs["observations"] = observations[case]
    _assert_window(window_inputs, "not_ready")


@pytest.mark.parametrize("reliable, status", [(False, "not_ready"), (None, "unknown")])
def test_window_reliability(window_inputs, reliable, status):
    slot = window_inputs["expected_timestamps"][0]
    window_inputs["observations"] = (_window_observation(slot, reliable=reliable),)
    _assert_window(window_inputs, status)


@pytest.mark.parametrize("kwh", [None, float("nan"), float("inf"), -float("inf"), -1.0])
@pytest.mark.parametrize("reliable", [True, None])
def test_window_invalid_value_is_definite_blocker(window_inputs, kwh, reliable):
    # A known invalid value outranks unknown reliability even on the same slot.
    slot = window_inputs["expected_timestamps"][0]
    window_inputs["observations"] = (_window_observation(slot, kwh, reliable),)
    _assert_window(window_inputs, "not_ready")


def test_window_naive_observation(window_inputs):
    slot = window_inputs["expected_timestamps"][0]
    window_inputs["observations"] = (_window_observation(slot.replace(tzinfo=None)),)
    _assert_window(window_inputs, "unknown")


@pytest.mark.parametrize("seconds_before", [0, 1])
def test_window_as_of_boundary(window_inputs, seconds_before):
    slot = window_inputs["expected_timestamps"][0]
    window_inputs["as_of"] = slot - timedelta(seconds=seconds_before)
    _assert_window(window_inputs, "not_ready" if seconds_before else "ready",
                   0 if seconds_before else 1)


@pytest.mark.parametrize("keep_expected", [True, False])
def test_window_extra_reading_cannot_substitute(window_inputs, keep_expected):
    slot = window_inputs["expected_timestamps"][0]
    extra = _window_observation(slot + timedelta(minutes=30))
    window_inputs["observations"] = (
        (_window_observation(slot), extra) if keep_expected else (extra,)
    )
    _assert_window(window_inputs, "ready" if keep_expected else "not_ready",
                   1 if keep_expected else 0)


def test_window_extra_unusable_reading_ignored_after_timestamp_validation(window_inputs):
    slot = window_inputs["expected_timestamps"][0]
    extra = _window_observation(slot + timedelta(minutes=30), None, None)
    window_inputs["observations"] += (extra,)
    _assert_window(window_inputs, "ready", 1)


@pytest.mark.parametrize("keep_expected", [True, False])
def test_window_naive_extra_overrides_completeness(window_inputs, keep_expected):
    slot = window_inputs["expected_timestamps"][0]
    extra = _window_observation((slot + timedelta(minutes=30)).replace(tzinfo=None))
    window_inputs["observations"] = (
        (_window_observation(slot), extra) if keep_expected else (extra,)
    )
    _assert_window(window_inputs, "unknown")


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "blocker", ["missing", "unreliable", "invalid-value", "future", "duplicate"]
)
def test_window_definite_blocker_outranks_other_slot_unknown(window_inputs, reverse, blocker):
    first = window_inputs["expected_timestamps"][0]
    second = first + timedelta(minutes=30)
    slots = (first, second)
    observations = [_window_observation(first, reliable=None)]
    if blocker == "unreliable":
        observations.append(_window_observation(second, reliable=False))
    elif blocker == "invalid-value":
        observations.append(_window_observation(second, kwh=None))
    elif blocker == "duplicate":
        observations.extend([_window_observation(second), _window_observation(second)])
    elif blocker == "future":
        observations.append(_window_observation(second))
    window_inputs.update(
        expected_timestamps=tuple(reversed(slots)) if reverse else slots,
        observations=tuple(observations),
        as_of=first if blocker == "future" else second,
    )
    _assert_window(window_inputs, "not_ready")


@pytest.mark.parametrize("window_status", ["ready", "not_ready", "unknown"])
@pytest.mark.parametrize(
    "baseline_status, peer_status, ready_result",
    [
        ("pass", "pass", "verified"),
        ("fail", "pass", "not_verified"),
        ("pass", "fail", "not_verified"),
        ("fail", "fail", "not_verified"),
        ("unknown", "pass", "insufficient_evidence"),
        ("pass", "unknown", "insufficient_evidence"),
        ("unknown", "unknown", "insufficient_evidence"),
        ("fail", "unknown", "insufficient_evidence"),
        ("unknown", "fail", "insufficient_evidence"),
    ],
)
def test_verification_summary_complete_truth_table(
    window_status, baseline_status, peer_status, ready_result,
):
    inputs = {
        "window": ObservationWindowResult(
            status=window_status,
            expected_count=1,
            usable_count=1 if window_status == "ready" else 0,
            reason="Window evidence fixture.",
        ),
        "baseline": ReturnToBaselineResult(
            status=baseline_status, reason="Baseline evidence fixture.",
        ),
        "peer": PeerAgreementResult(
            status=peer_status, reason="Peer evidence fixture.",
        ),
    }
    before = {name: value.model_dump() for name, value in inputs.items()}
    result = summarize_verification(**inputs)

    assert isinstance(result, VerificationSummaryResult)
    expected = ready_result if window_status == "ready" else "insufficient_evidence"
    assert result.status == expected
    assert result.window_status == window_status
    assert result.baseline_status == baseline_status
    assert result.peer_status == peer_status
    assert result.method_version == "verification_v1"
    assert result.reason
    assert summarize_verification(**inputs) == result
    assert {name: value.model_dump() for name, value in inputs.items()} == before


def _peer_history(target_values, peer_values, *, missing_peer_indices=(), cutoff_offset=12):
    start = datetime(2026, 9, 1, tzinfo=UTC)
    timestamps = [start + timedelta(hours=index) for index in range(len(target_values))]
    target = MeterSeries(
        meter_id="M1",
        readings=[
            ReadingPoint(timestamp=t, kwh=v)
            for t, v in zip(timestamps, target_values, strict=True)
        ],
    )
    peers = []
    for peer_id, values in zip(("M2", "M3"), peer_values, strict=True):
        peers.append(MeterSeries(
            meter_id=peer_id,
            readings=[
                ReadingPoint(timestamp=t, kwh=v)
                for index, (t, v) in enumerate(zip(timestamps, values, strict=True))
                if not (peer_id == "M3" and index in missing_peer_indices)
            ],
        ))
    cutoff = start + timedelta(hours=cutoff_offset)
    return target, tuple(peers), cutoff


def _build_peer_reference(target, peers, cutoff):
    return build_peer_agreement_reference(
        target=target, peers=peers, cutoff=cutoff, run_id=UUID(int=2),
    )


def test_peer_reference_stable_relationship_has_consistent_envelope():
    target, peers, cutoff = _peer_history([5.0] * 13, [[10.0] * 13, [10.0] * 13])
    result = _build_peer_reference(target, peers, cutoff)
    assert result.status == "answered"
    assert result.support_count == 12
    assert result.median == result.q1 == result.q3 == -50.0
    assert result.iqr == 0.0
    assert result.tolerance == 5.0
    assert result.lower == -55.0
    assert result.upper == -45.0
    assert all(isfinite(value) for value in (
        result.median, result.q1, result.q3, result.iqr,
        result.tolerance, result.lower, result.upper,
    ))
    assert result.lower == result.q1 - result.tolerance
    assert result.upper == result.q3 + result.tolerance


def test_peer_reference_preserves_signed_deviations():
    ratios = [0.5, 0.6, 0.7, 0.8] * 3
    target, peers, cutoff = _peer_history(
        [10.0 * ratio for ratio in ratios], [[10.0] * 12, [10.0] * 12],
    )
    result = _build_peer_reference(target, peers, cutoff)
    assert result.status == "answered"
    assert result.median < 0
    assert result.q1 < 0
    assert result.q3 < 0


def test_peer_reference_excludes_cutoff_and_future_readings():
    target, peers, cutoff = _peer_history([5.0] * 14, [[10.0] * 14, [10.0] * 14])
    result = _build_peer_reference(target, peers, cutoff)
    assert result.support_count == 12
    assert result.median == -50.0
    # Event/future data cannot change the pre-cutoff reference.
    for series in (target, *peers):
        series.readings[-2].kwh = 99999.0
        series.readings[-1].kwh = 99999.0
    assert _build_peer_reference(target, peers, cutoff) == result


def test_peer_reference_exactly_twelve_observations_is_enough():
    target, peers, cutoff = _peer_history([5.0] * 12, [[10.0] * 12, [10.0] * 12])
    result = _build_peer_reference(target, peers, cutoff)
    assert result.status == "answered"
    assert result.support_count == 12


def test_peer_reference_eleven_observations_is_unknown():
    target, peers, cutoff = _peer_history([5.0] * 11, [[10.0] * 11, [10.0] * 11])
    result = _build_peer_reference(target, peers, cutoff)
    assert result.status == "unknown"
    assert result.support_count == 11


@pytest.mark.parametrize("peer_mode", ["empty", "duplicate", "contains-target"])
def test_peer_reference_rejects_invalid_frozen_peers(peer_mode):
    target, peers, cutoff = _peer_history([5.0] * 12, [[10.0] * 12, [10.0] * 12])
    if peer_mode == "empty":
        peers = ()
    elif peer_mode == "duplicate":
        peers = (peers[0], peers[0])
    else:
        peers = (peers[0], target)
    result = _build_peer_reference(target, peers, cutoff)
    assert result.status == "unknown"


def test_peer_reference_excludes_timestamp_missing_a_frozen_peer():
    target, peers, cutoff = _peer_history(
        [5.0] * 14, [[10.0] * 14, [10.0] * 14], missing_peer_indices={4}, cutoff_offset=13,
    )
    result = _build_peer_reference(target, peers, cutoff)
    assert result.status == "answered"
    assert result.support_count == 12


def test_peer_reference_excludes_zero_peer_median_timestamp():
    target_values = [5.0] * 14
    p2_values = [10.0] * 14
    p3_values = [10.0] * 14
    p2_values[3] = p3_values[3] = 0.0
    target_values[3] = 0.0
    target, peers, cutoff = _peer_history(
        target_values, [p2_values, p3_values], cutoff_offset=13,
    )
    result = _build_peer_reference(target, peers, cutoff)
    assert result.status == "answered"
    assert result.support_count == 12


def test_peer_reference_insufficient_after_invalid_deviation_filtering():
    target_values = [5.0] * 13
    target_values[:2] = [float("nan"), float("inf")]
    target, peers, cutoff = _peer_history(target_values, [[10.0] * 13, [10.0] * 13])
    result = _build_peer_reference(target, peers, cutoff)
    assert result.status == "unknown"
    assert result.support_count == 10


def test_peer_reference_repeated_input_is_deterministic():
    target, peers, cutoff = _peer_history([5.0] * 12, [[10.0] * 12, [10.0] * 12])
    assert _build_peer_reference(target, peers, cutoff) == _build_peer_reference(
        target, peers, cutoff
    )


def test_peer_reference_naive_cutoff_is_rejected():
    target, peers, cutoff = _peer_history([5.0] * 12, [[10.0] * 12, [10.0] * 12])
    with pytest.raises(ValueError, match="timezone-aware"):
        _build_peer_reference(target, peers, cutoff.replace(tzinfo=None))
