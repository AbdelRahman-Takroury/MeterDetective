import pandas as pd
import pytest
import numpy as np

from advanced_analytics import (
    forecast_expected_usage,
    calculate_energy_balance,
    detect_isolation_forest_anomaly,
    analyze_weather_alignment,
    analyze_customer_der_context,
    calculate_anomaly_severity ,
    estimate_revenue_at_risk ,
    calculate_triage_priority
)




# ============================================================
# اختبار forecast_expected_usage
# ============================================================

def test_forecast_expected_usage():

    # داتا بسيطة نعرف النتيجة المتوقعة منها مسبقا
    df = pd.DataFrame({
        "Meter_ID": [
            "M1",
            "M1",
            "M1",
            "M1"
        ],

        "DateTime": [
            "2026-09-01 18:30",
            "2026-09-08 18:30",
            "2026-09-15 18:30",
            "2026-09-22 18:30"
        ],

        "KWH/hh (per half hour)": [
            1.0,
            1.2,
            1.4,
            100.0
        ]
    })

    result = forecast_expected_usage(
        readings_df=df,
        meter_id="M1",
        target_timestamp="2026-09-22 18:30",
        min_samples=3
    )

    assert result["status"] == "success"

    # لازم يستخدم القراءات الثلاث السابقة فقط
    # وما يستخدم قراءة يوم 22 نفسها
    assert result["support_count"] == 3

    # median لـ 1.0, 1.2, 1.4 = 1.2
    assert result["expected_kwh"] == 1.2

    assert result["method"] == "seasonal_weekday_halfhour"


# ============================================================
# اختبار Energy Balance بحالة Balanced
# ============================================================

def test_energy_balance_balanced():

    result = calculate_energy_balance(
        transformer_reading=9.30,
        meter_readings=[2.0, 3.0, 4.0],
        technical_loss_rate=0.03,
        tolerance_pct=0.05
    )

    assert result["status"] == "success"
    assert result["balance_status"] == "balanced"

    # مجموع العدادات = 9
    assert result["downstream_kwh"] == 9.0

    # 9 × 1.03 = 9.27
    assert abs(
        result["expected_transformer_kwh"] - 9.27
    ) < 0.0001


# ============================================================
# اختبار Energy Balance بحالة Imbalanced
# ============================================================

def test_energy_balance_imbalanced():

    result = calculate_energy_balance(
        transformer_reading=12.0,
        meter_readings=[2.0, 3.0, 4.0],
        technical_loss_rate=0.03,
        tolerance_pct=0.05
    )

    assert result["status"] == "success"
    assert result["balance_status"] == "imbalanced"
# ============================================================
# تجهيز داتا بسيطة لاختبار Isolation Forest
# ============================================================

# ============================================================
# تجهيز داتا لاختبار Isolation Forest
# ============================================================

def build_isolation_test_data(spike=False):

    # random generator ثابت عشان الاختبار يكون reproducible
    rng = np.random.default_rng(42)

    timestamps = pd.date_range(
        start="2026-01-01 00:00",
        periods=201,
        freq="30min"
    )

    # قراءات طبيعية حول 1 kWh مع تغير بسيط وطبيعي
    readings = 1.0 + rng.normal(
        loc=0.0,
        scale=0.05,
        size=201
    )

    # اخر قراءة هي القراءة الي بدنا نفحصها
    if spike:
        readings[-1] = 6.0

    return pd.DataFrame({
        "Meter_ID": ["M1"] * 201,
        "DateTime": timestamps,
        "KWH/hh (per half hour)": readings
    })
# ============================================================
# اختبار Isolation Forest مع قراءة طبيعية
# ============================================================

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
        random_state=42
    )

    assert result["status"] == "success"
    assert result["is_anomaly"] is False
    assert result["isolation_label"] == "normal"


# ============================================================
# اختبار Isolation Forest مع spike واضح
# ============================================================

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
        random_state=42
    )

    assert result["status"] == "success"
    assert result["is_anomaly"] is True
    assert result["isolation_label"] == "anomaly"


# ============================================================
# اختبار reproducibility
# ============================================================

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
        random_state=42
    )

    result_2 = detect_isolation_forest_anomaly(
        readings_df=df,
        meter_id="M1",
        target_timestamp=target_timestamp,
        contamination=0.05,
        random_state=42
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
# ============================================================
# اختبار Weather Alignment بحالة supporting
# ============================================================

def test_weather_alignment_supporting():

    weather_df = pd.DataFrame({
        "DateTime": [
            "2026-07-01 13:00",
            "2026-07-01 14:00"
        ],
        "temperature_c": [
            34.0,
            35.0
        ]
    })

    result = analyze_weather_alignment(
        weather_df=weather_df,
        target_timestamp="2026-07-01 13:30",
        actual_kwh=1.8,
        expected_kwh=1.0
    )

    assert result["status"] == "success"
    assert result["weather_condition"] == "hot"
    assert result["weather_evidence"] == "supporting"


# ============================================================
# اختبار Weather Alignment بحالة not supporting
# ============================================================

def test_weather_alignment_not_supporting():

    weather_df = pd.DataFrame({
        "DateTime": [
            "2026-07-01 13:00",
            "2026-07-01 14:00"
        ],
        "temperature_c": [
            22.0,
            23.0
        ]
    })

    result = analyze_weather_alignment(
        weather_df=weather_df,
        target_timestamp="2026-07-01 13:30",
        actual_kwh=1.8,
        expected_kwh=1.0
    )

    assert result["status"] == "success"
    assert result["weather_condition"] == "moderate"
    assert result["weather_evidence"] == "not_supporting"   
    # ============================================================
# اختبار EV context
# ============================================================

def test_customer_der_ev_supporting():

    readings_df = pd.DataFrame({
        "Meter_ID": ["M1"] * 5,
        "DateTime": [
            "2026-07-01 18:00",
            "2026-07-01 18:30",
            "2026-07-01 19:00",
            "2026-07-01 19:30",
            "2026-07-01 20:00"
        ],
        "KWH/hh (per half hour)": [
            1.0,
            1.1,
            1.0,
            1.1,
            2.0
        ]
    })

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M1"],
        "Has_EV": [True],
        "Has_Solar": [False]
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M1",
        target_timestamp="2026-07-01 20:00",
        actual_kwh=2.0,
        expected_kwh=1.0
    )

    assert result["status"] == "success"
    assert result["ev"]["has_ev"] == True
    assert result["ev"]["evidence"] == "supporting"

    # ما لازم نحول evidence الى proof
    assert len(result["ev"]["limitations"]) > 0


# ============================================================
# اختبار Solar context
# ============================================================

def test_customer_der_solar_supporting():

    readings_df = pd.DataFrame({
        "Meter_ID": ["M2"] * 5,
        "DateTime": [
            "2026-07-01 10:00",
            "2026-07-01 10:30",
            "2026-07-01 11:00",
            "2026-07-01 11:30",
            "2026-07-01 12:00"
        ],
        "KWH/hh (per half hour)": [
            1.0,
            1.0,
            0.9,
            1.0,
            0.5
        ]
    })

    metadata_df = pd.DataFrame({
        "Meter_ID": ["M2"],
        "Has_EV": [False],
        "Has_Solar": [True]
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M2",
        target_timestamp="2026-07-01 12:00",
        actual_kwh=0.5,
        expected_kwh=1.0
    )

    assert result["status"] == "success"
    assert result["solar"]["has_solar"] == True
    assert result["solar"]["evidence"] == "supporting"


# ============================================================
# اختبار عدم وجود metadata للعداد
# ============================================================

def test_customer_der_missing_metadata():

    readings_df = pd.DataFrame({
        "Meter_ID": ["M3"],
        "DateTime": ["2026-07-01 12:00"],
        "KWH/hh (per half hour)": [1.0]
    })

    metadata_df = pd.DataFrame({
        "Meter_ID": ["OTHER_METER"],
        "Has_EV": [False],
        "Has_Solar": [False]
    })

    result = analyze_customer_der_context(
        readings_df=readings_df,
        customer_metadata_df=metadata_df,
        meter_id="M3",
        target_timestamp="2026-07-01 12:00",
        actual_kwh=1.0,
        expected_kwh=1.0
    )

    assert result["status"] == "unknown"
    # ============================================================
# اختبار Hybrid Anomaly Severity
# ============================================================

def test_anomaly_severity_calculation():

    result = calculate_anomaly_severity(
        usage_deviation_pct=-0.80,
        rule_based_score=80,
        isolation_score=-0.10,
        peer_network_score=0.50
    )

    assert result["status"] == "success"
    assert result["severity_score"] == 68.0

    assert result["normalized_components"]["usage_deviation"] == 0.8
    assert result["normalized_components"]["rule_based"] == 0.8
    assert result["normalized_components"]["isolation_forest"] == 0.5
    assert result["normalized_components"]["peer_network"] == 0.5


# ============================================================
# اختبار ان Severity لا تتجاوز 100
# ============================================================

def test_anomaly_severity_is_capped_at_100():

    result = calculate_anomaly_severity(
        usage_deviation_pct=2.0,
        rule_based_score=150,
        isolation_score=-0.50,
        peer_network_score=2.0
    )

    assert result["status"] == "success"
    assert result["severity_score"] == 100.0


# ============================================================
# اختبار missing severity input
# ============================================================

def test_anomaly_severity_missing_input():

    result = calculate_anomaly_severity(
        usage_deviation_pct=None,
        rule_based_score=80,
        isolation_score=-0.10,
        peer_network_score=0.50
    )

    assert result["status"] == "unknown"
    # ============================================================
# اختبار Revenue at Risk الطبيعي
# ============================================================

def test_revenue_at_risk_calculation():

    result = estimate_revenue_at_risk(
        expected_kwh=10.0,
        reliable_observed_kwh=4.0,
        tariff_jod_per_kwh=0.12,
        tariff_version="test-tariff-v1",
        recoverability_low=0.60,
        recoverability_base=0.80,
        recoverability_high=1.00,
        data_reliable=True
    )

    assert result["status"] == "success"

    assert result["expected_missing_kwh"] == 6.0
    assert result["gross_exposure_jod"] == pytest.approx(0.72)

    assert result["revenue_at_risk_jod"]["low"] == pytest.approx(0.432)
    assert result["revenue_at_risk_jod"]["base"] == pytest.approx(0.576)
    assert result["revenue_at_risk_jod"]["high"] == pytest.approx(0.72)


# ============================================================
# اختبار Missing Tariff
# ============================================================

def test_revenue_at_risk_missing_tariff():

    result = estimate_revenue_at_risk(
        expected_kwh=10.0,
        reliable_observed_kwh=4.0,
        tariff_jod_per_kwh=None,
        data_reliable=True
    )

    assert result["status"] == "unknown"
    assert result["revenue_at_risk_jod"] is None


# ============================================================
# اختبار Unreliable Reading
# ============================================================

def test_revenue_at_risk_unreliable_reading():

    result = estimate_revenue_at_risk(
        expected_kwh=10.0,
        reliable_observed_kwh=4.0,
        tariff_jod_per_kwh=0.12,
        data_reliable=False
    )

    assert result["status"] == "unknown"
# ============================================================
# اختبار Revenue at Risk الطبيعي
# ============================================================

def test_revenue_at_risk_calculation():

    result = estimate_revenue_at_risk(
        expected_kwh=10.0,
        reliable_observed_kwh=4.0,
        tariff_jod_per_kwh=0.12,
        tariff_version="test-tariff-v1",
        recoverability_low=0.60,
        recoverability_base=0.80,
        recoverability_high=1.00,
        data_reliable=True
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


# ============================================================
# اختبار Missing Tariff
# ============================================================

def test_revenue_at_risk_missing_tariff():

    result = estimate_revenue_at_risk(
        expected_kwh=10.0,
        reliable_observed_kwh=4.0,
        tariff_jod_per_kwh=None,
        data_reliable=True
    )

    # Missing tariff لازم يكون Unknown
    # مش Revenue = 0
    assert result["status"] == "unknown"
    assert result["revenue_at_risk_jod"] is None


# ============================================================
# اختبار Unreliable Reading
# ============================================================

def test_revenue_at_risk_unreliable_reading():

    result = estimate_revenue_at_risk(
        expected_kwh=10.0,
        reliable_observed_kwh=4.0,
        tariff_jod_per_kwh=0.12,
        data_reliable=False
    )

    # ما بنطلع رقم مالي من بيانات غير موثوقة
    assert result["status"] == "unknown"
# ============================================================
# اختبار حساب Triage Priority
# ============================================================

def test_triage_priority_calculation():

    result = calculate_triage_priority(
        case_id="CASE-001",

        technical_severity=80,
        scope_ratio=0.50,

        revenue_at_risk_jod=6.0,
        revenue_reference_jod=10.0,

        recurrence_score=0.50,
        upstream_shared_score=0.40,
        data_confidence=0.90,
        waiting_sla_score=0.60,

        active_queue=[
            {
                "case_id": "CASE-A",
                "priority_score": 90
            },
            {
                "case_id": "CASE-B",
                "priority_score": 70
            },
            {
                "case_id": "CASE-C",
                "priority_score": 40
            }
        ]
    )

    assert result["status"] == "success"

    # Technical:
    # 80/100 * 25 = 20

    # Scope:
    # 0.50 * 20 = 10

    # Revenue:
    # 6/10 = 0.60
    # 0.60 * 20 = 12

    # Recurrence:
    # 0.50 * 10 = 5

    # Upstream:
    # 0.40 * 10 = 4

    # Confidence:
    # 0.90 * 10 = 9

    # Waiting:
    # 0.60 * 5 = 3

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
        abs=0.01
    )
# ============================================================
# اختبار ان Rank يتغير عندما تتغير Active Queue
# ============================================================

def test_triage_rank_changes_with_active_queue():

    first_result = calculate_triage_priority(
        case_id="CASE-001",

        technical_severity=80,
        scope_ratio=0.50,

        revenue_at_risk_jod=6.0,
        revenue_reference_jod=10.0,

        recurrence_score=0.50,
        upstream_shared_score=0.40,
        data_confidence=0.90,
        waiting_sla_score=0.60,

        active_queue=[
            {
                "case_id": "CASE-A",
                "priority_score": 90
            },
            {
                "case_id": "CASE-C",
                "priority_score": 40
            }
        ]
    )

    assert first_result["priority_score"] == 63.0

    # فقط CASE-A اعلى منها
    assert first_result["rank"] == 2


    second_result = calculate_triage_priority(
        case_id="CASE-001",

        technical_severity=80,
        scope_ratio=0.50,

        revenue_at_risk_jod=6.0,
        revenue_reference_jod=10.0,

        recurrence_score=0.50,
        upstream_shared_score=0.40,
        data_confidence=0.90,
        waiting_sla_score=0.60,

        active_queue=[
            {
                "case_id": "CASE-A",
                "priority_score": 90
            },
            {
                "case_id": "CASE-B",
                "priority_score": 70
            },
            {
                "case_id": "CASE-C",
                "priority_score": 40
            }
        ]
    )

    # دخلت حالة جديدة score تبعها 70
    # فصار في حالتين اعلى من CASE-001
    assert second_result["rank"] == 3

    # نفس الحالة ونفس inputs
    # لذلك Priority Score نفسه ما تغير
    assert second_result["priority_score"] == 63.0
# ============================================================
# اختبار عدم Double Counting للـ Recurrence
# ============================================================

def test_triage_recurrence_not_double_counted():

    without_recurrence = calculate_triage_priority(
        case_id="CASE-001",

        technical_severity=60,
        scope_ratio=0.40,

        revenue_at_risk_jod=5.0,
        revenue_reference_jod=10.0,

        recurrence_score=0.0,
        upstream_shared_score=0.30,
        data_confidence=0.80,
        waiting_sla_score=0.40,

        active_queue=[]
    )

    with_full_recurrence = calculate_triage_priority(
        case_id="CASE-001",

        # نفس Technical Severity
        technical_severity=60,

        scope_ratio=0.40,

        revenue_at_risk_jod=5.0,
        revenue_reference_jod=10.0,

        # التغيير الوحيد
        recurrence_score=1.0,

        upstream_shared_score=0.30,
        data_confidence=0.80,
        waiting_sla_score=0.40,

        active_queue=[]
    )

    assert without_recurrence["status"] == "success"
    assert with_full_recurrence["status"] == "success"

    score_difference = (
        with_full_recurrence["priority_score"]
        - without_recurrence["priority_score"]
    )

    # Recurrence وزنها 10
    # لذلك الانتقال من 0 الى 1 لازم يزيد 10 نقاط فقط
    assert score_difference == pytest.approx(10.0)

    assert (
        with_full_recurrence["contributions"]["recurrence"]
        == 10.0
    )
# ============================================================
# اختبار Priority Band العالي
# ============================================================

def test_triage_priority_p1_band():

    result = calculate_triage_priority(
        case_id="CASE-HIGH",

        technical_severity=100,
        scope_ratio=1.0,

        revenue_at_risk_jod=10.0,
        revenue_reference_jod=10.0,

        recurrence_score=1.0,
        upstream_shared_score=1.0,
        data_confidence=1.0,
        waiting_sla_score=1.0,

        active_queue=[]
    )

    assert result["status"] == "success"
    assert result["priority_score"] == 100.0
    assert result["priority_band"] == "P1"
    assert result["rank"] == 1
    assert result["percentile"] == 100.0
                   