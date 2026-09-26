import json

import numpy as np
import pandas as pd
import pytest

from advanced_analytics import (
    adapt_day3_anomaly_event_for_severity,
    adapt_day3_quality_for_day5,
    adapt_day4_peer_comparison_for_severity,
    adapt_day4_shared_incident_for_triage,
    adapt_energy_balance_for_triage,
    analyze_customer_der_context,
    analyze_weather_alignment,
    build_severity_calibration_report,
    calculate_anomaly_severity,
    calculate_energy_balance,
    calculate_triage_priority,
    detect_isolation_forest_anomaly,
    estimate_revenue_at_risk,
    forecast_expected_usage,
)

# ============================================================
# اختبار forecast_expected_usage
# ============================================================

def test_forecast_expected_usage():
    # داتا بسيطة نعرف النتيجة المتوقعة منها مسبقا
    df = pd.DataFrame({
        "Meter_ID": ["M1"] * 4,
        "DateTime": [
            "2026-09-01 18:30",
            "2026-09-08 18:30",
            "2026-09-15 18:30",
            "2026-09-22 18:30",
        ],
        "KWH/hh (per half hour)": [
            1.0,
            1.2,
            1.4,
            100.0,
        ],
    })

    result = forecast_expected_usage(
        readings_df=df,
        meter_id="M1",
        target_timestamp="2026-09-22 18:30",
        min_samples=3,
    )

    assert result["status"] == "success"

    # لازم يستخدم القراءات الثلاث السابقة فقط
    # وما يستخدم قراءة يوم 22 نفسها
    assert result["support_count"] == 3

    # median لـ 1.0, 1.2, 1.4 = 1.2
    assert result["expected_kwh"] == 1.2
    assert result["method"] == "seasonal_weekday_halfhour"


def test_forecast_rejects_duplicate_meter_timestamps():
    df = pd.DataFrame({
        "Meter_ID": ["M1", "M1", "M1"],
        "DateTime": [
            "2026-09-01 18:30",
            "2026-09-01 18:30",
            "2026-09-08 18:30",
        ],
        "KWH/hh (per half hour)": [1.0, 1.1, 1.2],
    })

    result = forecast_expected_usage(
        readings_df=df,
        meter_id="M1",
        target_timestamp="2026-09-15 18:30",
    )

    assert result["status"] == "error"
    assert "Duplicate timestamps" in result["reason"]


def test_forecast_rejects_nonfinite_reading():
    df = pd.DataFrame({
        "Meter_ID": ["M1", "M1", "M1"],
        "DateTime": [
            "2026-09-01 18:30",
            "2026-09-08 18:30",
            "2026-09-15 18:30",
        ],
        "KWH/hh (per half hour)": [1.0, np.inf, 1.2],
    })

    result = forecast_expected_usage(
        readings_df=df,
        meter_id="M1",
        target_timestamp="2026-09-22 18:30",
    )

    assert result["status"] == "error"


def test_forecast_ignores_missing_target_reading():
    df = pd.DataFrame({
        "Meter_ID": ["M1"] * 4,
        "DateTime": [
            "2026-09-01 18:30",
            "2026-09-08 18:30",
            "2026-09-15 18:30",
            "2026-09-22 18:30",
        ],
        "KWH/hh (per half hour)": [
            1.0,
            1.2,
            1.4,
            None,
        ],
    })

    result = forecast_expected_usage(
        readings_df=df,
        meter_id="M1",
        target_timestamp="2026-09-22 18:30",
        min_samples=3,
    )

    assert result["status"] == "success"
    assert result["expected_kwh"] == pytest.approx(
        1.2
    )


def test_forecast_ignores_invalid_future_reading():
    df = pd.DataFrame({
        "Meter_ID": ["M1"] * 4,
        "DateTime": [
            "2026-09-01 18:30",
            "2026-09-08 18:30",
            "2026-09-15 18:30",
            "2026-09-29 18:30",
        ],
        "KWH/hh (per half hour)": [
            1.0,
            1.2,
            1.4,
            np.inf,
        ],
    })

    result = forecast_expected_usage(
        readings_df=df,
        meter_id="M1",
        target_timestamp="2026-09-22 18:30",
        min_samples=3,
    )

    assert result["status"] == "success"
    assert result["expected_kwh"] == pytest.approx(
        1.2
    )


def test_forecast_ignores_invalid_unrelated_meter():
    df = pd.DataFrame({
        "Meter_ID": [
            "M1",
            "M1",
            "M1",
            "M2",
        ],
        "DateTime": [
            "2026-09-01 18:30",
            "2026-09-08 18:30",
            "2026-09-15 18:30",
            "2026-09-10 18:30",
        ],
        "KWH/hh (per half hour)": [
            1.0,
            1.2,
            1.4,
            np.inf,
        ],
    })

    result = forecast_expected_usage(
        readings_df=df,
        meter_id="M1",
        target_timestamp="2026-09-22 18:30",
        min_samples=3,
    )

    assert result["status"] == "success"


def test_forecast_ignores_duplicate_future_timestamp():
    df = pd.DataFrame({
        "Meter_ID": ["M1"] * 5,
        "DateTime": [
            "2026-09-01 18:30",
            "2026-09-08 18:30",
            "2026-09-15 18:30",
            "2026-09-29 18:30",
            "2026-09-29 18:30",
        ],
        "KWH/hh (per half hour)": [
            1.0,
            1.2,
            1.4,
            1.5,
            1.6,
        ],
    })

    result = forecast_expected_usage(
        readings_df=df,
        meter_id="M1",
        target_timestamp="2026-09-22 18:30",
        min_samples=3,
    )

    assert result["status"] == "success"


# ============================================================
# اختبار Energy Balance
# ============================================================

def test_energy_balance_balanced():
    result = calculate_energy_balance(
        transformer_reading=9.30,
        meter_readings=[2.0, 3.0, 4.0],
        technical_loss_rate=0.03,
        tolerance_pct=0.05,
    )

    assert result["status"] == "success"
    assert result["balance_status"] == "balanced"

    # مجموع العدادات = 9
    assert result["downstream_kwh"] == 9.0

    # 9 × 1.03 = 9.27
    assert abs(
        result["expected_transformer_kwh"] - 9.27
    ) < 0.0001


def test_energy_balance_imbalanced():
    result = calculate_energy_balance(
        transformer_reading=12.0,
        meter_readings=[2.0, 3.0, 4.0],
        technical_loss_rate=0.03,
        tolerance_pct=0.05,
    )

    assert result["status"] == "success"
    assert result["balance_status"] == "imbalanced"
    assert result["signed_difference_kwh"] > 0


@pytest.mark.parametrize(
    "transformer_reading,meter_readings",
    [
        (-1.0, [1.0, 2.0]),
        (3.0, [1.0, -2.0]),
        (np.inf, [1.0, 2.0]),
    ],
)
def test_energy_balance_rejects_invalid_readings(
    transformer_reading,
    meter_readings,
):
    result = calculate_energy_balance(
        transformer_reading=transformer_reading,
        meter_readings=meter_readings,
    )

    assert result["status"] == "error"


# ============================================================
# تجهيز داتا لاختبار Isolation Forest
# ============================================================

def build_isolation_test_data(spike=False):
    # random generator ثابت عشان الاختبار يكون reproducible
    rng = np.random.default_rng(42)

    timestamps = pd.date_range(
        start="2026-01-01 00:00",
        periods=201,
        freq="30min",
    )

    # قراءات طبيعية حول 1 kWh مع تغير بسيط وطبيعي
    readings = 1.0 + rng.normal(
        loc=0.0,
        scale=0.05,
        size=201,
    )

    # اخر قراءة هي القراءة الي بدنا نفحصها
    if spike:
        readings[-1] = 6.0

    return pd.DataFrame({
        "Meter_ID": ["M1"] * 201,
        "DateTime": timestamps,
        "KWH/hh (per half hour)": readings,
    })


def test_isolation_forest_normal_reading():
    df = build_isolation_test_data(
        spike=False
    )

    target_timestamp = df.iloc[-1]["DateTime"]

    result = detect_isolation_forest_anomaly(
        readings_df=df,
        meter_id="M1",
        target_timestamp=target_timestamp,
        contamination=0.05,
        random_state=42,
    )

    assert result["status"] == "success"
    assert result["is_anomaly"] is False
    assert result["isolation_label"] == "normal"


def test_isolation_forest_detects_spike():
    df = build_isolation_test_data(
        spike=True
    )

    target_timestamp = df.iloc[-1]["DateTime"]

    result = detect_isolation_forest_anomaly(
        readings_df=df,
        meter_id="M1",
        target_timestamp=target_timestamp,
        contamination=0.05,
        random_state=42,
    )

    assert result["status"] == "success"
    assert result["is_anomaly"] is True
    assert result["isolation_label"] == "anomaly"


def test_isolation_forest_reproducible():
    df = build_isolation_test_data(
        spike=True
    )

    target_timestamp = df.iloc[-1]["DateTime"]

    result_1 = detect_isolation_forest_anomaly(
        readings_df=df,
        meter_id="M1",
        target_timestamp=target_timestamp,
        contamination=0.05,
        random_state=42,
    )

    result_2 = detect_isolation_forest_anomaly(
        readings_df=df,
        meter_id="M1",
        target_timestamp=target_timestamp,
        contamination=0.05,
        random_state=42,
    )

    assert result_1["status"] == "success"
    assert result_2["status"] == "success"

    # نفس البيانات + نفس random_state لازم يعطوا نفس القرار
    assert result_1["is_anomaly"] == result_2["is_anomaly"]
    assert result_1["isolation_label"] == result_2["isolation_label"]

    # والـ score لازم يكون نفسه تقريباً
    assert result_1["isolation_score"] == pytest.approx(
        result_2["isolation_score"]
    )


def test_isolation_forest_rejects_duplicate_timestamp():
    df = build_isolation_test_data()
    duplicate = df.iloc[[50]].copy()
    df = pd.concat([df, duplicate], ignore_index=True)

    result = detect_isolation_forest_anomaly(
        readings_df=df,
        meter_id="M1",
        target_timestamp=df.iloc[200]["DateTime"],
    )

    assert result["status"] == "error"


def test_isolation_forest_target_features_require_continuity():
    df = build_isolation_test_data()

    target_timestamp = df.iloc[-1]["DateTime"]

    # نحذف القراءة السابقة مباشرة عن الهدف
    df = df[
        df["DateTime"] != target_timestamp - pd.Timedelta(minutes=30)
    ]

    result = detect_isolation_forest_anomaly(
        readings_df=df,
        meter_id="M1",
        target_timestamp=target_timestamp,
    )

    assert result["status"] == "unknown"
    assert "temporally continuous" in result["reason"]


def test_isolation_forest_ignores_invalid_future_reading():
    df = build_isolation_test_data()

    target_timestamp = df.iloc[-1][
        "DateTime"
    ]

    future_row = pd.DataFrame({
        "Meter_ID": ["M1"],
        "DateTime": [
            target_timestamp
            + pd.Timedelta(minutes=30)
        ],
        "KWH/hh (per half hour)": [
            np.inf
        ],
    })

    df = pd.concat(
        [df, future_row],
        ignore_index=True,
    )

    result = detect_isolation_forest_anomaly(
        readings_df=df,
        meter_id="M1",
        target_timestamp=target_timestamp,
    )

    assert result["status"] == "success"


def test_isolation_forest_missing_target_is_unknown():
    df = build_isolation_test_data()

    target_timestamp = df.iloc[-1][
        "DateTime"
    ]

    df.loc[
        df["DateTime"] == target_timestamp,
        "KWH/hh (per half hour)",
    ] = np.nan

    result = detect_isolation_forest_anomaly(
        readings_df=df,
        meter_id="M1",
        target_timestamp=target_timestamp,
    )

    assert result["status"] == "unknown"
    assert "missing" in result["reason"].lower()


# ============================================================
# اختبار Weather Alignment
# ============================================================

def test_weather_alignment_supporting():
    weather_df = pd.DataFrame({
        "DateTime": [
            "2026-07-01 13:00",
            "2026-07-01 14:00",
        ],
        "temperature_c": [
            34.0,
            35.0,
        ],
    })

    result = analyze_weather_alignment(
        weather_df=weather_df,
        target_timestamp="2026-07-01 13:30",
        actual_kwh=1.8,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"
    assert result["weather_condition"] == "hot"
    assert result["weather_evidence"] == "supporting"

    # عند التعادل نختار الماضي 13:00 وليس المستقبل 14:00
    assert result["weather_timestamp"].startswith(
        "2026-07-01T13:00:00"
    )


def test_weather_alignment_not_supporting():
    weather_df = pd.DataFrame({
        "DateTime": [
            "2026-07-01 13:00",
            "2026-07-01 14:00",
        ],
        "temperature_c": [
            22.0,
            23.0,
        ],
    })

    result = analyze_weather_alignment(
        weather_df=weather_df,
        target_timestamp="2026-07-01 13:30",
        actual_kwh=1.8,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"
    assert result["weather_condition"] == "moderate"
    assert result["weather_evidence"] == "not_supporting"


def test_weather_alignment_rejects_nan_actual():
    weather_df = pd.DataFrame({
        "DateTime": ["2026-07-01 13:00"],
        "temperature_c": [34.0],
    })

    result = analyze_weather_alignment(
        weather_df=weather_df,
        target_timestamp="2026-07-01 13:00",
        actual_kwh=np.nan,
        expected_kwh=1.0,
    )

    assert result["status"] == "error"


def test_weather_online_mode_does_not_use_future_observation():
    weather_df = pd.DataFrame({
        "DateTime": [
            "2026-07-01 12:00",
            "2026-07-01 13:40",
        ],
        "temperature_c": [
            20.0,
            40.0,
        ],
    })

    result = analyze_weather_alignment(
        weather_df=weather_df,
        target_timestamp="2026-07-01 13:30",
        actual_kwh=1.8,
        expected_kwh=1.0,
        weather_mode="online",
        alignment_tolerance_minutes=120,
    )

    assert result["status"] == "success"
    assert result["weather_timestamp"].startswith(
        "2026-07-01T12:00:00"
    )


# ============================================================
# اختبار DER context
# ============================================================

def build_der_readings(
    values,
    start="2026-07-01 18:00",
):
    timestamps = pd.date_range(
        start=start,
        periods=len(values),
        freq="30min",
    )

    return pd.DataFrame({
        "Meter_ID": ["M1"] * len(values),
        "DateTime": timestamps,
        "KWH/hh (per half hour)": values,
    })


def test_customer_der_ev_supporting_with_real_persistence():
    # اخر ثلاث intervals مرتفعة مقارنة بالـ expected = 1.0
    readings_df = build_der_readings(
        [1.0, 1.0, 1.3, 1.4, 2.0]
    )

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [True],
        "Has_Solar": [False],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 20:00",
        actual_kwh=2.0,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"
    assert result["ev"]["has_ev"] is True
    assert result["ev"]["evidence"] == "supporting"
    assert result["persistence"]["is_persistent"] is True

    # ما لازم نحول evidence الى proof
    assert len(result["ev"]["limitations"]) > 0


def test_customer_der_does_not_claim_persistence_from_single_spike():
    readings_df = build_der_readings(
        [1.0, 1.0, 1.0, 1.0, 2.0]
    )

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [True],
        "Has_Solar": [False],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 20:00",
        actual_kwh=2.0,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"
    assert result["persistence"]["is_persistent"] is False
    assert result["ev"]["evidence"] == "not_supporting"


def test_customer_der_gap_breaks_persistence():
    readings_df = build_der_readings(
        [1.0, 1.0, 1.3, 1.4, 2.0]
    )

    # نحذف 19:30، وهي القراءة السابقة مباشرة للهدف
    readings_df = readings_df[
        readings_df["DateTime"] != pd.Timestamp("2026-07-01 19:30")
    ]

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [True],
        "Has_Solar": [False],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 20:00",
        actual_kwh=2.0,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"
    assert result["persistence"]["is_persistent"] is False
    assert result["persistence"]["continuity_broken"] is True


def test_customer_der_solar_supporting():
    readings_df = pd.DataFrame({
        "Meter_ID": ["M2"] * 5,
        "DateTime": [
            "2026-07-01 10:00",
            "2026-07-01 10:30",
            "2026-07-01 11:00",
            "2026-07-01 11:30",
            "2026-07-01 12:00",
        ],
        "KWH/hh (per half hour)": [
            1.0,
            1.0,
            0.9,
            1.0,
            0.5,
        ],
    })

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M2"],
        "Has_EV": [False],
        "Has_Solar": [True],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M2",
        target_timestamp="2026-07-01 12:00",
        actual_kwh=0.5,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"
    assert result["solar"]["has_solar"] is True
    assert result["solar"]["evidence"] == "supporting"


def test_customer_der_missing_metadata():
    readings_df = pd.DataFrame({
        "Meter_ID": ["M3"],
        "DateTime": ["2026-07-01 12:00"],
        "KWH/hh (per half hour)": [1.0],
    })

    metadata_df = pd.DataFrame({
        "Meter_ID": ["OTHER_METER"],
        "Has_EV": [False],
        "Has_Solar": [False],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M3",
        target_timestamp="2026-07-01 12:00",
        actual_kwh=1.0,
        expected_kwh=1.0,
    )

    assert result["status"] == "unknown"


def test_customer_der_handles_numpy_boolean_metadata():
    readings_df = build_der_readings(
        [1.0, 1.0, 1.0, 1.0, 2.0]
    )

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [np.bool_(False)],
        "Has_Solar": [np.bool_(True)],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 20:00",
        actual_kwh=2.0,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"
    assert result["ev"]["has_ev"] is False
    assert result["solar"]["has_solar"] is True
    assert result["ev"]["evidence"] == "not_supporting"

    # النتيجة لازم تكون JSON serializable
    json.dumps(result)


def test_customer_der_treats_nan_metadata_as_unknown():
    readings_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "DateTime": ["2026-07-01 12:00"],
        "KWH/hh (per half hour)": [1.0],
    })

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [np.nan],
        "Has_Solar": [np.nan],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 12:00",
        actual_kwh=1.0,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"
    assert result["ev"]["has_ev"] is None
    assert result["solar"]["has_solar"] is None
    assert result["ev"]["evidence"] == "unknown"
    assert result["solar"]["evidence"] == "unknown"

    json.dumps(result)


def test_customer_der_ignores_invalid_future_reading():
    readings_df = build_der_readings(
        [1.0, 1.0, 1.3, 1.4, 2.0]
    )

    future_row = pd.DataFrame({
        "Meter_ID": ["M1"],
        "DateTime": ["2026-07-01 20:30"],
        "KWH/hh (per half hour)": [np.inf],
    })

    readings_df = pd.concat(
        [readings_df, future_row],
        ignore_index=True,
    )

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [True],
        "Has_Solar": [False],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 20:00",
        actual_kwh=2.0,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"


def test_customer_der_rejects_invalid_reading_inside_persistence_window():
    readings_df = build_der_readings(
        [1.0, 1.0, 1.3, np.inf, 2.0]
    )

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [True],
        "Has_Solar": [False],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 20:00",
        actual_kwh=2.0,
        expected_kwh=1.0,
    )

    assert result["status"] == "error"


def test_customer_der_exposes_constant_reference_assumption():
    readings_df = build_der_readings(
        [1.0, 1.0, 1.3, 1.4, 2.0]
    )

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [True],
        "Has_Solar": [False],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 20:00",
        actual_kwh=2.0,
        expected_kwh=1.0,
    )

    assert result["status"] == "success"
    assert result["persistence"][
        "expected_reference_mode"
    ] == "target_expected_constant"
    assert result["persistence"][
        "limitation"
    ] is not None


def test_customer_der_per_interval_expected_prevents_false_persistence():
    readings_df = build_der_readings(
        [1.0, 1.0, 1.3, 1.4, 2.0]
    )

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [True],
        "Has_Solar": [False],
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 20:00",
        actual_kwh=2.0,
        expected_kwh=1.0,
        persistence_expected_kwh_by_timestamp={
            "2026-07-01 19:30": 1.4,
            "2026-07-01 19:00": 1.3,
            "2026-07-01 18:30": 1.0,
            "2026-07-01 18:00": 1.0,
        },
    )

    assert result["status"] == "success"
    assert result["persistence"][
        "expected_reference_mode"
    ] == "per_interval_expected_kwh"
    assert result["persistence"][
        "is_persistent"
    ] is False
    assert result["ev"]["evidence"] == "not_supporting"


# ============================================================
# Day 3 / Day 4 contract adapters
# ============================================================

@pytest.mark.parametrize(
    (
        "anomaly_type",
        "deviation_pct",
        "expected_fraction",
    ),
    [
        ("drop", 80.0, -0.80),
        ("spike", 80.0, 0.80),
        ("zero_period", 100.0, -1.0),
    ],
)
def test_day3_magnitude_anomaly_adapter(
    anomaly_type,
    deviation_pct,
    expected_fraction,
):
    result = adapt_day3_anomaly_event_for_severity(
        anomaly_type=anomaly_type,
        deviation_pct=deviation_pct,
        event_severity=80,
    )

    assert result["status"] == "success"
    assert result["usage_deviation_fraction"] == pytest.approx(
        expected_fraction
    )

    # مهم:
    # ما نعيد Day 3 severity كـ rule evidence
    assert result["rule_based_score"] == 0.0
    assert result["evidence_ownership"] == "usage_deviation"


@pytest.mark.parametrize(
    (
        "anomaly_type",
        "event_severity",
    ),
    [
        ("gap", 80.0),
        ("flatline", 50.0),
    ],
)
def test_day3_structural_anomaly_adapter(
    anomaly_type,
    event_severity,
):
    result = adapt_day3_anomaly_event_for_severity(
        anomaly_type=anomaly_type,
        event_severity=event_severity,
    )

    assert result["status"] == "success"
    assert result["usage_deviation_fraction"] == 0.0
    assert result["rule_based_score"] == event_severity
    assert result["evidence_ownership"] == "structural_rule"


@pytest.mark.parametrize(
    (
        "peer_deviation_pct",
        "expected_score",
        "expected_direction",
    ),
    [
        (-80.0, 0.80, "below_peers"),
        (40.0, 0.40, "above_peers"),
        (0.0, 0.0, "aligned_with_peers"),
        (150.0, 1.0, "above_peers"),
    ],
)
def test_day4_peer_adapter(
    peer_deviation_pct,
    expected_score,
    expected_direction,
):
    result = adapt_day4_peer_comparison_for_severity(
        peer_deviation_pct
    )

    assert result["status"] == "success"
    assert result["peer_deviation_score"] == pytest.approx(
        expected_score
    )
    assert result["direction"] == expected_direction


def test_quality_adapter_maps_contracts_without_mixing_confidences():
    result = adapt_day3_quality_for_day5(
        quality_score=82.0,
        reliable=True,
        quality_status="warning",
    )

    assert result["status"] == "success"
    assert result["data_confidence"] == pytest.approx(0.82)
    assert result["data_reliable"] is True


def test_shared_adapter_uses_affected_fraction_for_scope():
    result = adapt_day4_shared_incident_for_triage(
        shared_status="answered",
        affected_fraction=0.50,
        incident_type="shared",
        shared_confidence=0.90,
    )

    assert result["status"] == "success"
    assert result["scope_ratio"] == pytest.approx(0.50)
    assert result["shared_classification_confidence"] == pytest.approx(0.90)
    assert result["confidence_usage"] == (
        "metadata_only_not_triage_data_confidence"
    )


def test_energy_balance_adapter_provides_independent_upstream_signal():
    energy = calculate_energy_balance(
        transformer_reading=12.0,
        meter_readings=[2.0, 3.0, 4.0],
        technical_loss_rate=0.03,
        tolerance_pct=0.05,
    )

    result = adapt_energy_balance_for_triage(
        energy,
        excess_difference_reference=0.50,
    )

    assert result["status"] == "success"
    assert 0.0 < result["upstream_evidence_score"] <= 1.0


# ============================================================
# Hybrid Anomaly Severity
# ============================================================

def test_anomaly_severity_after_contract_adapters():
    anomaly = adapt_day3_anomaly_event_for_severity(
        anomaly_type="drop",
        deviation_pct=80.0,
        event_severity=80.0,
    )

    peer = adapt_day4_peer_comparison_for_severity(
        -50.0
    )

    result = calculate_anomaly_severity(
        usage_deviation_fraction=anomaly[
            "usage_deviation_fraction"
        ],
        # drop magnitude already belongs to usage
        rule_based_score=anomaly[
            "rule_based_score"
        ],
        isolation_score=-0.10,
        peer_deviation_score=peer[
            "peer_deviation_score"
        ],
    )

    assert result["status"] == "success"

    # Usage:
    # 0.80 * 35 = 28
    #
    # Rule:
    # 0 * 25 = 0
    #
    # Isolation:
    # 0.10 / 0.20 = 0.50
    # 0.50 * 20 = 10
    #
    # Peer:
    # 0.50 * 20 = 10
    #
    # Total = 48
    assert result["severity_score"] == 48.0


def test_anomaly_severity_is_capped_at_100():
    result = calculate_anomaly_severity(
        usage_deviation_fraction=2.0,
        rule_based_score=100.0,
        isolation_score=-0.50,
        peer_deviation_score=1.0,
    )

    assert result["status"] == "success"
    assert result["severity_score"] == 100.0


def test_anomaly_severity_missing_input():
    result = calculate_anomaly_severity(
        usage_deviation_fraction=None,
        rule_based_score=0.0,
        isolation_score=-0.10,
        peer_deviation_score=0.50,
    )

    assert result["status"] == "unknown"


def test_anomaly_severity_rejects_invalid_peer_scale():
    result = calculate_anomaly_severity(
        usage_deviation_fraction=0.50,
        rule_based_score=0.0,
        isolation_score=-0.10,

        # Day 5 contract هو 0..1.
        # 80% من Day 4 لازم يمر بالـ adapter أول.
        peer_deviation_score=80.0,
    )

    assert result["status"] == "error"


def test_severity_calibration_report_passes_monotonic_checks():
    report = build_severity_calibration_report()

    assert report["status"] == "success"
    assert all(report["checks"].values())
    assert report["scores"]["all_zero"] == 0.0
    assert report["scores"]["all_max"] == 100.0


# ============================================================
# Revenue at Risk
# ============================================================

def revenue_kwargs():
    return {
        "expected_kwh": 10.0,
        "reliable_observed_kwh": 4.0,
        "tariff_jod_per_kwh": 0.12,
        "tariff_version": "test-tariff-v1",
        "tariff_source": "fixture:tariffs:test",
        "tariff_bracket_name": "0-300 kWh",
        "recoverability_low": 0.60,
        "recoverability_base": 0.80,
        "recoverability_high": 1.00,
        "data_reliable": True,
        "quality_score": 90.0,
        "calculation_timestamp": "2026-09-26T10:00:00Z",
        "calculation_window_start": "2026-09-26T09:30:00Z",
        "calculation_window_end": "2026-09-26T10:00:00Z",
        "forecast_reference": "forecast:M1:2026-09-26T10:00Z",
        "observed_reference": "reading:M1:2026-09-26T10:00Z",
        "quality_reference": "quality:M1:2026-09-26",
    }


def test_revenue_at_risk_calculation():
    result = estimate_revenue_at_risk(
        **revenue_kwargs()
    )

    assert result["status"] == "success"

    # 10 - 4 = 6 kWh missing
    assert result["expected_missing_kwh"] == 6.0

    # 6 * 0.12 = 0.72 JOD
    assert result["gross_exposure_jod"] == pytest.approx(0.72)

    # Low / Base / High scenarios
    assert result["revenue_at_risk_jod"]["low"] == pytest.approx(0.432)
    assert result["revenue_at_risk_jod"]["base"] == pytest.approx(0.576)
    assert result["revenue_at_risk_jod"]["high"] == pytest.approx(0.72)

    assert result["tariff"]["version"] == "test-tariff-v1"
    assert result["tariff"]["source"] == "fixture:tariffs:test"
    assert "fixed charges" in result["exclusions"]


def test_revenue_at_risk_missing_tariff():
    kwargs = revenue_kwargs()
    kwargs["tariff_jod_per_kwh"] = None

    result = estimate_revenue_at_risk(
        **kwargs
    )

    # Missing tariff لازم يكون Unknown
    # مش Revenue = 0
    assert result["status"] == "unknown"
    assert result["revenue_at_risk_jod"] is None


def test_revenue_at_risk_unreliable_reading():
    kwargs = revenue_kwargs()
    kwargs["data_reliable"] = False

    result = estimate_revenue_at_risk(
        **kwargs
    )

    # ما بنطلع رقم مالي من بيانات غير موثوقة
    assert result["status"] == "unknown"


def test_revenue_at_risk_requires_audit_provenance():
    kwargs = revenue_kwargs()
    kwargs["quality_reference"] = None

    result = estimate_revenue_at_risk(
        **kwargs
    )

    assert result["status"] == "unknown"
    assert result["revenue_at_risk_jod"] is None


def test_revenue_overrecording_does_not_become_missing_energy():
    kwargs = revenue_kwargs()
    kwargs["expected_kwh"] = 10.0
    kwargs["reliable_observed_kwh"] = 16.0

    result = estimate_revenue_at_risk(
        **kwargs
    )

    assert result["status"] == "success"
    assert result["expected_missing_kwh"] == 0.0
    assert result["gross_exposure_jod"] == 0.0


# ============================================================
# Triage Priority
# ============================================================

def triage_kwargs():
    return {
        "case_id": "CASE-001",
        "technical_severity": 80,
        "scope_ratio": 0.50,
        "revenue_at_risk_jod": 6.0,
        "revenue_reference_jod": 10.0,
        "recurrence_score": 0.50,
        "upstream_evidence_score": 0.40,
        "data_confidence": 0.90,
        "waiting_sla_score": 0.60,
    }


def test_triage_priority_calculation():
    kwargs = triage_kwargs()
    kwargs["active_queue"] = [
        {
            "case_id": "CASE-A",
            "priority_score": 90,
        },
        {
            "case_id": "CASE-B",
            "priority_score": 70,
        },
        {
            "case_id": "CASE-C",
            "priority_score": 40,
        },
    ]

    result = calculate_triage_priority(
        **kwargs
    )

    assert result["status"] == "success"

    # Technical:
    # 80/100 * 25 = 20
    #
    # Scope:
    # 0.50 * 20 = 10
    #
    # Revenue:
    # 6/10 = 0.60
    # 0.60 * 20 = 12
    #
    # Recurrence:
    # 0.50 * 10 = 5
    #
    # Upstream:
    # 0.40 * 10 = 4
    #
    # Confidence:
    # 0.90 * 10 = 9
    #
    # Waiting:
    # 0.60 * 5 = 3
    #
    # Total:
    # 20 + 10 + 12 + 5 + 4 + 9 + 3 = 63
    assert result["priority_score"] == 63.0
    assert result["priority_band"] == "P2"

    # 90 و 70 اعلى من 63
    # اذا current case ترتيبها الثالث
    assert result["rank"] == 3

    # الثلاث حالات القديمة + الحالة الحالية
    assert result["queue_size"] == 4

    # فقط CASE-C = 40 اقل من 63
    # 1 من 3 حالات اخرى = 33.33%
    assert result["percentile"] == pytest.approx(
        33.33,
        abs=0.01,
    )


def test_triage_rank_changes_with_active_queue():
    kwargs = triage_kwargs()
    kwargs["active_queue"] = [
        {
            "case_id": "CASE-A",
            "priority_score": 90,
        },
        {
            "case_id": "CASE-C",
            "priority_score": 40,
        },
    ]

    first_result = calculate_triage_priority(
        **kwargs
    )

    assert first_result["priority_score"] == 63.0
    assert first_result["rank"] == 2

    kwargs["active_queue"] = [
        {
            "case_id": "CASE-A",
            "priority_score": 90,
        },
        {
            "case_id": "CASE-B",
            "priority_score": 70,
        },
        {
            "case_id": "CASE-C",
            "priority_score": 40,
        },
    ]

    second_result = calculate_triage_priority(
        **kwargs
    )

    assert second_result["rank"] == 3
    assert second_result["priority_score"] == 63.0


def test_triage_rejects_duplicate_case_ids():
    kwargs = triage_kwargs()
    kwargs["active_queue"] = [
        {
            "case_id": "CASE-A",
            "priority_score": 90,
        },
        {
            "case_id": "CASE-A",
            "priority_score": 70,
        },
    ]

    result = calculate_triage_priority(
        **kwargs
    )

    assert result["status"] == "error"
    assert "Duplicate case_id" in result["reason"]


def test_triage_selects_explicit_base_revenue_scenario():
    kwargs = triage_kwargs()
    kwargs["revenue_at_risk_jod"] = {
        "low": 4.0,
        "base": 6.0,
        "high": 8.0,
    }
    kwargs["active_queue"] = []

    result = calculate_triage_priority(
        **kwargs
    )

    assert result["status"] == "success"
    assert result["revenue"]["scenario_used"] == "base"
    assert result["revenue"]["selected_jod"] == 6.0


def test_triage_recurrence_not_double_counted():
    base = triage_kwargs()
    base.update({
        "technical_severity": 60,
        "scope_ratio": 0.40,
        "revenue_at_risk_jod": 5.0,
        "upstream_evidence_score": 0.30,
        "data_confidence": 0.80,
        "waiting_sla_score": 0.40,
        "active_queue": [],
    })

    without_recurrence = calculate_triage_priority(
        **{
            **base,
            "recurrence_score": 0.0,
        }
    )

    with_full_recurrence = calculate_triage_priority(
        **{
            **base,
            "recurrence_score": 1.0,
        }
    )

    score_difference = (
        with_full_recurrence["priority_score"]
        - without_recurrence["priority_score"]
    )

    # Recurrence وزنها 10
    # لذلك الانتقال من 0 الى 1 لازم يزيد 10 نقاط فقط
    assert score_difference == pytest.approx(10.0)


def test_triage_priority_p1_band():
    result = calculate_triage_priority(
        case_id="CASE-HIGH",
        technical_severity=100,
        scope_ratio=1.0,
        revenue_at_risk_jod=10.0,
        revenue_reference_jod=10.0,
        recurrence_score=1.0,
        upstream_evidence_score=1.0,
        data_confidence=1.0,
        waiting_sla_score=1.0,
        active_queue=[],
    )

    assert result["status"] == "success"
    assert result["priority_score"] == 100.0
    assert result["priority_band"] == "P1"
    assert result["rank"] == 1
    assert result["percentile"] == 100.0


def test_triage_ties_have_same_rank_and_are_not_counted_lower():
    kwargs = triage_kwargs()
    kwargs["active_queue"] = [
        {
            "case_id": "CASE-TIE",
            "priority_score": 63.0,
        },
        {
            "case_id": "CASE-LOW",
            "priority_score": 40.0,
        },
    ]

    result = calculate_triage_priority(
        **kwargs
    )

    assert result["status"] == "success"
    assert result["rank"] == 1
    assert result["percentile"] == pytest.approx(50.0)
