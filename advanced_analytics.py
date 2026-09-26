# Day 5

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

# اسم عمود القراءة الموجود في Developer A fixture
READING_COLUMN = "KWH/hh (per half hour)"

# Day 5 يعمل داخليا على UTC.
# القراءات القديمة التي لا تحتوي timezone يتم تفسيرها كـ UTC
# حتى تتوافق مع قرار الـ integration الجديد.
EXPECTED_INTERVAL_MINUTES = 30


def _parse_utc_timestamp(value):
    """
    تحويل أي timestamp صالح إلى UTC.

    إذا كان الوقت بدون timezone يتم تفسيره كـ UTC
    بما يتوافق مع Developer A integration.
    """
    return pd.to_datetime(
        value,
        errors="coerce",
        utc=True
    )


def _is_numeric_value(value):
    """
    Python تعتبر bool نوع من int،
    لذلك نرفض bool بشكل صريح في الحسابات الرقمية.
    """
    return (
        not isinstance(value, (bool, np.bool_))
        and isinstance(value, (int, float, np.number))
    )


def _validate_finite_number(
    value,
    input_name,
    minimum=None,
    maximum=None,
    allow_none=False
):
    """
    Helper صغير لتوحيد validation للأرقام المستخدمة في Day 5.
    """
    if value is None:
        if allow_none:
            return None
        return {
            "status": "unknown",
            "reason": f"{input_name} is required"
        }

    if not _is_numeric_value(value):
        return {
            "status": "error",
            "reason": f"{input_name} must be numeric"
        }

    value = float(value)

    if not np.isfinite(value):
        return {
            "status": "error",
            "reason": f"{input_name} must be finite"
        }

    if minimum is not None and value < minimum:
        return {
            "status": "error",
            "reason": f"{input_name} must be >= {minimum}"
        }

    if maximum is not None and value > maximum:
        return {
            "status": "error",
            "reason": f"{input_name} must be <= {maximum}"
        }

    return None


def _prepare_meter_time_window(
    readings_df,
    meter_id,
    start_timestamp=None,
    end_timestamp=None,
    include_start=True,
    include_end=False
):
    """
    تجهيز window زمنية لعداد واحد فقط.

    مهم:
    - نحدد عداد الهدف اولا.
    - نحول timestamps قبل اختيار الـ window.
    - validation لقيم الاستهلاك يتم بعد اختيار الـ window.
    - duplicate timestamps نفحصها داخل الـ window المطلوبة فقط.
    - مشكلة في عداد ثاني ما توقف تحليل العداد المطلوب.
    """

    required_columns = {
        "Meter_ID",
        "DateTime",
        READING_COLUMN
    }

    missing_columns = required_columns - set(
        readings_df.columns
    )

    if missing_columns:
        return None, {
            "status": "error",
            "reason": (
                "Missing required columns: "
                f"{sorted(missing_columns)}"
            )
        }

    # نجيب عداد الهدف فقط قبل اي validation
    # حتى مشكلة في عداد ثاني ما تأثر علينا.
    meter_df = readings_df[
        readings_df["Meter_ID"] == meter_id
    ].copy()

    if meter_df.empty:
        return None, {
            "status": "unknown",
            "reason": "No readings found for meter"
        }

    # لازم نعرف وقت كل row حتى نقدر نحدد
    # اذا هي داخل الـ window المطلوبة او لا.
    parsed_time = pd.to_datetime(
        meter_df["DateTime"],
        errors="coerce",
        utc=True
    )

    if parsed_time.isna().any():
        return None, {
            "status": "error",
            "reason": "Meter readings contain invalid timestamps"
        }

    meter_df["DateTime"] = parsed_time

    # تجهيز حدود الـ window اذا كانت موجودة.
    if start_timestamp is not None:
        start_timestamp = _parse_utc_timestamp(
            start_timestamp
        )

        if pd.isna(start_timestamp):
            return None, {
                "status": "error",
                "reason": "Invalid start_timestamp"
            }

        if include_start:
            meter_df = meter_df[
                meter_df["DateTime"] >= start_timestamp
            ]
        else:
            meter_df = meter_df[
                meter_df["DateTime"] > start_timestamp
            ]

    if end_timestamp is not None:
        end_timestamp = _parse_utc_timestamp(
            end_timestamp
        )

        if pd.isna(end_timestamp):
            return None, {
                "status": "error",
                "reason": "Invalid end_timestamp"
            }

        if include_end:
            meter_df = meter_df[
                meter_df["DateTime"] <= end_timestamp
            ]
        else:
            meter_df = meter_df[
                meter_df["DateTime"] < end_timestamp
            ]

    meter_df = meter_df.sort_values(
        "DateTime"
    ).reset_index(
        drop=True
    )

    # duplicate خارج الـ window ما بهم التحليل الحالي.
    # لكن duplicate داخل الـ window المطلوبة
    # يعتبر data-quality error.
    if meter_df["DateTime"].duplicated().any():
        return None, {
            "status": "error",
            "reason": (
                "Duplicate timestamps found for meter "
                "inside the selected time window"
            )
        }

    return meter_df, None


def _validate_meter_reading_values(
    meter_df,
    context_name,
    require_nonnegative=True,
    missing_value_status="error"
):
    """
    فحص قيم الاستهلاك بعد ما نحدد الـ window المطلوبة.

    هيك target او future reading خارج التحليل
    ما تقدر تغير نتيجة حساب تاريخي.

    missing_value_status يسمح لبعض الادوات مثل
    Isolation Forest تعتبر missing target = Unknown
    بدل ما تعتبرها tool failure.
    """

    if meter_df is None:
        return None, {
            "status": "error",
            "reason": (
                f"{context_name} reading window is unavailable"
            )
        }

    # empty window ليست data corruption.
    # الـ caller يقرر اذا معناها Unknown او insufficient data.
    if meter_df.empty:
        return meter_df.copy(), None

    raw_values = meter_df[
        READING_COLUMN
    ]

    # missing value تختلف عن string غير رقمي.
    missing_mask = raw_values.isna()

    if missing_mask.any():
        return None, {
            "status": missing_value_status,
            "reason": (
                f"{context_name} contains missing meter readings"
            )
        }

    numeric_values = pd.to_numeric(
        raw_values,
        errors="coerce"
    )

    # بما ان missing الحقيقي انمسك فوق،
    # اي NaN جديد هون يعني قيمة non-numeric.
    if numeric_values.isna().any():
        return None, {
            "status": "error",
            "reason": (
                f"{context_name} contains non-numeric meter readings"
            )
        }

    numeric_values = numeric_values.astype(
        float
    )

    if not np.isfinite(
        numeric_values.to_numpy()
    ).all():
        return None, {
            "status": "error",
            "reason": (
                f"{context_name} must contain finite numeric meter readings"
            )
        }

    # المشروع الحالي يتعامل مع consumption readings غير السالبة.
    # اذا اضفنا net export لاحقا لازم نغير هذا العقد بشكل صريح.
    if (
        require_nonnegative
        and (numeric_values < 0).any()
    ):
        return None, {
            "status": "error",
            "reason": (
                f"{context_name} contains negative readings outside "
                "the current non-negative consumption contract"
            )
        }

    validated_df = meter_df.copy()

    validated_df[
        READING_COLUMN
    ] = numeric_values

    return validated_df, None


def forecast_expected_usage(
    readings_df,
    meter_id,
    target_timestamp,
    min_samples=3
):
    """
    توقع الاستهلاك المتوقع للعداد في وقت محدد
    باستخدام نمط موسمي بسيط من القراءات التاريخية.

    يتم استخدام القراءات السابقة فقط لمنع
    تسرب بيانات مستقبلية إلى التوقع.
    """

    # 1. التحقق من البيانات

    if readings_df is None or readings_df.empty:
        return {
            "status": "error",
            "reason": "readings_df is required and cannot be empty"
        }

    if not meter_id or not str(meter_id).strip():
        return {
            "status": "error",
            "reason": "meter_id is required"
        }

    if target_timestamp is None:
        return {
            "status": "error",
            "reason": "target_timestamp is required"
        }

    if (
        isinstance(min_samples, bool)
        or not isinstance(min_samples, (int, np.integer))
        or min_samples < 1
    ):
        return {
            "status": "error",
            "reason": "min_samples must be a positive integer"
        }

    # 2. التأكد من وجود الأعمدة المطلوبة
    # validation الفعلي يتم بعد اختيار historical window.

    # 3. تحويل وقت الهدف إلى datetime

    target_timestamp = _parse_utc_timestamp(
        target_timestamp
    )

    if pd.isna(target_timestamp):
        return {
            "status": "error",
            "reason": "Invalid target_timestamp"
        }

    # 4. تجهيز القراءات التاريخية فقط

    # بنحدد التاريخ المطلوب قبل validation لقيم الاستهلاك.
    # هيك target gap او future invalid reading
    # ما يغير نتيجة forecast مبنية على تاريخ سليم.
    historical_df, preparation_error = (
        _prepare_meter_time_window(
            readings_df=readings_df,
            meter_id=meter_id,
            end_timestamp=target_timestamp,
            include_end=False
        )
    )

    if preparation_error is not None:
        return preparation_error

    if historical_df.empty:
        return {
            "status": "unknown",
            "reason": (
                "No historical readings available "
                "before target timestamp"
            )
        }

    historical_df, validation_error = (
        _validate_meter_reading_values(
            meter_df=historical_df,
            context_name="Historical forecast window"
        )
    )

    if validation_error is not None:
        return validation_error

    # 5. جلب القراءات التاريخية للعداد

    # historical_df هون صار يحتوي فقط
    # القراءات السابقة للوقت الي بدنا نتوقعه.
    # target reading نفسها او future readings
    # ما الها اي تأثير على forecast.

    #
    # 6. تحديد الوقت الموسمي الي بدنا نقارنه

    # عشان يجيب القراءات السابقة للزمن الي بدنا نتوقعه
    target_weekday = target_timestamp.dayofweek
    target_hour = target_timestamp.hour
    target_minute = target_timestamp.minute

    #
    # 7. جلب القراءات من نفس اليوم ونفس الوقت

    # مثلا لو الوقت المطلوب Tuesday 18:30
    # بنجيب Tuesdays السابقة على الساعة 18:30 فقط
    seasonal_df = historical_df[
        (historical_df["DateTime"].dt.dayofweek == target_weekday)
        & (historical_df["DateTime"].dt.hour == target_hour)
        & (historical_df["DateTime"].dt.minute == target_minute)
    ].copy()

    # 8. حساب الاستهلاك المتوقع

    if len(seasonal_df) >= min_samples:

        # اذا عندنا عدد كافي من القراءات لنفس اليوم ونفس الوقت
        # بنستخدم median عشان نقلل تأثير القراءات الشاذة
        expected_kwh = seasonal_df[
            READING_COLUMN
        ].median()

        method = "seasonal_weekday_halfhour"

        support_count = len(seasonal_df)

    else:

        # اذا ما كان عندنا بيانات كافية لنفس اليوم
        # بنستخدم نفس الوقت من الايام السابقة كـ fallback
        fallback_df = historical_df[
            (historical_df["DateTime"].dt.hour == target_hour)
            & (historical_df["DateTime"].dt.minute == target_minute)
        ].copy()

        if len(fallback_df) >= min_samples:

            # نحسب median لنفس الوقت من الايام السابقة
            expected_kwh = fallback_df[
                READING_COLUMN
            ].median()

            method = "seasonal_halfhour_fallback"

            # عدد القراءات الي استخدمناها عشان نحسب التوقع
            support_count = len(fallback_df)

        else:

            # اذا حتى الـ fallback ما عنده قراءات كافية
            # ما بنقدر نعطي توقع موثوق
            return {
                "status": "unknown",
                "meter_id": meter_id,
                "target_timestamp": target_timestamp.isoformat(),
                "reason": "Insufficient historical readings for forecast",
                "seasonal_samples": len(seasonal_df),
                "fallback_samples": len(fallback_df)
            }

    # 9. النتيجة النهائية للتوقع

    return {
        "status": "success",
        "meter_id": meter_id,
        "target_timestamp": target_timestamp.isoformat(),

        # الاستهلاك المتوقع للعداد
        "expected_kwh": float(expected_kwh),

        # الطريقة الي استخدمناها عشان نحسب التوقع
        "method": method,

        # عدد القراءات الي اعتمدنا عليها
        "support_count": support_count,

        # اقل عدد قراءات مطلوب حتى نعتبر التوقع صالح
        "min_samples": int(min_samples),

        # مصدر الوقت المستخدم داخل Day 5
        "timezone_policy": "UTC",

        # نسخة طريقة التوقع عشان نقدر نتتبع الحسابات لاحقا
        "method_version": "forecast_v2"
    }


def calculate_energy_balance(
    transformer_reading,
    meter_readings,
    technical_loss_rate=0.03,
    tolerance_pct=0.05
):  # الهامش الي بسمح فيه قبل ما اقول انه في عندي imbalance
    """
    حساب توازن الطاقة بين قراءة المحول
    ومجموع قراءات العدادات المتصلة فيه.

    يتم احتساب نسبة التيكنكال لوس المتوقعة
    ثم مقارنة القراءة المتوقعة للمحول مع القراءة الفعلية.
    """

    #       -
    # 1. التحقق من البيانات
    #        -

    if transformer_reading is None:
        return {
            "status": "error",
            "reason": "transformer_reading is required"
        }

    if meter_readings is None or len(meter_readings) == 0:
        return {
            "status": "error",
            "reason": "meter_readings are required"
        }

    config_inputs = {
        "technical_loss_rate": technical_loss_rate,
        "tolerance_pct": tolerance_pct
    }

    for input_name, input_value in config_inputs.items():
        error = _validate_finite_number(
            input_value,
            input_name,
            minimum=0.0,
            maximum=1.0
        )

        if error is not None:
            return error

    #         -
    # 2. تحويل القراءات الى ارقام والتأكد انها صالحة
    #          -

    try:
        transformer_reading = float(
            transformer_reading
        )

        meter_readings = [
            float(reading)
            for reading in meter_readings
        ]

    except (TypeError, ValueError):
        return {
            "status": "error",
            "reason": "Transformer and meter readings must be numeric"
        }

    # التأكد انه ما عندنا NaN او infinity
    all_readings = [
        transformer_reading,
        *meter_readings
    ]

    if not all(
        np.isfinite(reading)
        for reading in all_readings
    ):
        return {
            "status": "error",
            "reason": "Transformer and meter readings must be finite"
        }

    # المشروع الحالي يفترض استهلاك غير سالب.
    if any(
        reading < 0
        for reading in all_readings
    ):
        return {
            "status": "error",
            "reason": (
                "Transformer and meter readings cannot be negative "
                "under the current consumption contract"
            )
        }

    # 3. حساب مجموع قراءات العدادات

    downstream_kwh = sum(
        meter_readings
    )

    # 4. حساب القراءة المتوقعة للمحول

    # نضيف نسبة الفقد التقني على مجموع قراءات العدادات
    expected_transformer_kwh = downstream_kwh * (
        1 + float(technical_loss_rate)
    )

    # 5. حساب الفرق بين القراءة الفعلية والمتوقعة

    # Signed residual مفيد لاحقا لمعرفة اتجاه الـ mismatch.
    signed_difference_kwh = (
        transformer_reading
        - expected_transformer_kwh
    )

    difference_kwh = abs(
        signed_difference_kwh
    )

    #                 -
    # 6. حماية القسمة على صفر
    #                  -

    # اذا كان الاستهلاك المتوقع للمحول صفر
    # ما بنقدر نحسب نسبة الفرق بالقسمة عليه
    if expected_transformer_kwh == 0:

        # اذا قراءة المحول كمان صفر معناها ما في فرق
        if transformer_reading == 0:
            difference_pct = 0.0
            signed_difference_pct = 0.0

        # اذا المتوقع صفر لكن المحول عنده قراءة
        # بنعتبر الحالة غير متوازنة بدون ما نقسم على صفر
        else:
            return {
                "status": "success",
                "balance_status": "imbalanced",
                "downstream_kwh": float(downstream_kwh),
                "expected_transformer_kwh": 0.0,
                "actual_transformer_kwh": float(transformer_reading),
                "difference_kwh": float(difference_kwh),
                "signed_difference_kwh": float(signed_difference_kwh),
                "difference_pct": None,
                "signed_difference_pct": None,
                "tolerance_pct": float(tolerance_pct),
                "technical_loss_rate": float(technical_loss_rate),
                "reason": (
                    "Expected transformer usage is zero "
                    "while actual usage is non-zero"
                ),
                "method_version": "energy_balance_v2"
            }

    else:

        # حساب نسبة الفرق بين القراءة الفعلية والمتوقعة
        difference_pct = (
            difference_kwh
            / expected_transformer_kwh
        )

        signed_difference_pct = (
            signed_difference_kwh
            / expected_transformer_kwh
        )

    #                   -
    # 7. تحديد اذا كان المحول متوازن او لا
    #  
    balance_status = (
    "balanced"
    if difference_pct <= tolerance_pct
    else "imbalanced"
)                

   

    #                    -
    # 8. النتيجة النهائية

    return {
        "status": "success",

        # مجموع استهلاك العدادات المتصلة بالمحول
        "downstream_kwh": float(downstream_kwh),

        # القراءة المتوقعة للمحول بعد اضافة الفقد التقني
        "expected_transformer_kwh": float(
            expected_transformer_kwh
        ),

        # القراءة الفعلية للمحول
        "actual_transformer_kwh": float(
            transformer_reading
        ),

        # مقدار الفرق بالكيلو واط ساعة
        "difference_kwh": float(
            difference_kwh
        ),

        # Signed difference يحافظ على اتجاه الـ mismatch.
        "signed_difference_kwh": float(
            signed_difference_kwh
        ),

        # نسبة الفرق بين القراءة الفعلية والمتوقعة
        "difference_pct": float(
            difference_pct
        ),

        "signed_difference_pct": float(
            signed_difference_pct
        ),

        # الهامش الي بسمح فيه قبل ما اقول انه في عندي imbalance
        "tolerance_pct": float(
            tolerance_pct
        ),

        # نسبة الفقد التقني المستخدمة بالحساب
        "technical_loss_rate": float(
            technical_loss_rate
        ),

        # النتيجة النهائية للتوازن
        "balance_status": balance_status,

        # نسخة طريقة الحساب عشان نقدر نتتبع النتائج لاحقا
        "method_version": "energy_balance_v2"
    }

def detect_isolation_forest_anomaly(
    readings_df,
    meter_id,
    target_timestamp,
    contamination=0.05,
    random_state=42  # random_state=42 مهم لأنه يخلي التدريب reproducible
):
    """
    اكتشاف الـ anomaly
    باستخدام Isolation Forest.

    يدرب الموديل على historical readings السابقة فقط.
    """

    # 1. التحقق من البيانات

    if readings_df is None or readings_df.empty:
        return {
            "status": "error",
            "reason": "readings_df is required and cannot be empty"
        }

    if not meter_id or not str(meter_id).strip():
        return {
            "status": "error",
            "reason": "meter_id is required"
        }

    if target_timestamp is None:
        return {
            "status": "error",
            "reason": "target_timestamp is required"
        }

    contamination_error = _validate_finite_number(
        contamination,
        "contamination",
        minimum=0.000001,
        maximum=0.5
    )

    if contamination_error is not None:
        return contamination_error

    if (
        isinstance(random_state, bool)
        or not isinstance(random_state, (int, np.integer))
    ):
        return {
            "status": "error",
            "reason": "random_state must be an integer"
        }

    # 2. التأكد من وجود الاعمدة المطلوبة
    # validation الفعلي يتم بعد اختيار كل window المطلوبة.

    # 3. تجهيز الوقت والبيانات
    target_timestamp = _parse_utc_timestamp(
        target_timestamp
    )

    if pd.isna(target_timestamp):
        return {
            "status": "error",
            "reason": "Invalid target_timestamp"
        }

    # 4. جلب تاريخ العداد السابق فقط

    # training data لازم تكون فقط قبل target.
    # future readings ما لازم تدخل validation
    # ولا تغير نتيجة الموديل.
    historical_df, preparation_error = (
        _prepare_meter_time_window(
            readings_df=readings_df,
            meter_id=meter_id,
            end_timestamp=target_timestamp,
            include_end=False
        )
    )

    if preparation_error is not None:
        return preparation_error

    if historical_df.empty:
        return {
            "status": "unknown",
            "reason": (
                "No historical readings available "
                "before target timestamp"
            )
        }

    historical_df, validation_error = (
        _validate_meter_reading_values(
            meter_df=historical_df,
            context_name="Isolation Forest historical window"
        )
    )

    if validation_error is not None:
        return validation_error

    # ترتيب القراءات حسب الوقت
    historical_df = historical_df.sort_values(
        "DateTime"
    ).reset_index(
        drop=True
    )

    # 5. Feature Engineering

    # ما بنخلي rolling window تعبر gap زمني.
    # كل segment جديد يبدأ بعد أي فرق ليس 30 دقيقة.
    historical_df["_segment_id"] = (
        historical_df["DateTime"]
        .diff()
        .ne(pd.Timedelta(minutes=EXPECTED_INTERVAL_MINUTES))
        .cumsum()
    )

    # حساب نسبة التغير عن القراءة السابقة
    # استخدمنا symmetric change عشان نتجنب مشكلة القسمة على صفر
    previous_values = historical_df.groupby(
        "_segment_id"
    )[READING_COLUMN].shift(1)

    current_values = historical_df[
        READING_COLUMN
    ]

    change_denominator = (
        current_values.abs()
        + previous_values.abs()
    )

    # نعمل الحساب بالـ mask بدل np.where
    # حتى ما يصير division warning على صفوف denominator = 0.
    change_pct = pd.Series(
        np.nan,
        index=historical_df.index,
        dtype=float
    )

    valid_previous = previous_values.notna()
    zero_denominator = (
        valid_previous
        & (change_denominator == 0)
    )
    nonzero_denominator = (
        valid_previous
        & (change_denominator != 0)
    )

    change_pct.loc[
        zero_denominator
    ] = 0.0

    change_pct.loc[
        nonzero_denominator
    ] = (
        2
        * (
            current_values.loc[nonzero_denominator]
            - previous_values.loc[nonzero_denominator]
        )
        / change_denominator.loc[nonzero_denominator]
    )

    historical_df["change_pct"] = change_pct

    # حساب متوسط اخر 6 قراءات سابقة
    # shift(1) عشان القراءة الحالية ما تدخل بحساب المتوسط الخاص فيها
    historical_df["rolling_mean"] = historical_df.groupby(
        "_segment_id"
    )[READING_COLUMN].transform(
        lambda series: (
            series
            .shift(1)
            .rolling(
                window=6,
                min_periods=6
            )
            .mean()
        )
    )

    # حساب مقدار التذبذب في اخر 6 قراءات سابقة
    historical_df["rolling_std"] = historical_df.groupby(
        "_segment_id"
    )[READING_COLUMN].transform(
        lambda series: (
            series
            .shift(1)
            .rolling(
                window=6,
                min_periods=6
            )
            .std()
        )
    )

    # 6. تجهيز الـ features للموديل
    feature_columns = [
        READING_COLUMN,
        "change_pct",
        "rolling_mean",
        "rolling_std"
    ]

    # حذف الصفوف الي ما اكتملت فيها الـ features
    # هذا الحذف صار بعد segment-aware feature engineering،
    # لذلك gap ما يربط قراءات غير متجاورة.
    model_df = historical_df.dropna(
        subset=feature_columns
    ).copy()

    # التأكد انه عندنا بيانات كافية لتدريب الموديل
    if len(model_df) < 20:
        return {
            "status": "unknown",
            "reason": "Insufficient historical data for Isolation Forest",
            "available_samples": len(model_df),
            "required_samples": 20
        }

    # تجهيز البيانات الي رح تدخل للموديل
    X_train = model_df[
        feature_columns
    ]

    # 7. تدريب Isolation Forest
    model = IsolationForest(
        contamination=float(contamination),
        random_state=int(random_state)
    )

    model.fit(
        X_train
    )

    # 8. جلب القراءة المستهدفة

    # target يتم فحصها بشكل منفصل عن training history.
    # missing target في gap event ما تمنع تدريب الموديل،
    # لكنها تعني ان Isolation Forest ما عندها قيمة حتى تعمل score.
    target_df, target_preparation_error = (
        _prepare_meter_time_window(
            readings_df=readings_df,
            meter_id=meter_id,
            start_timestamp=target_timestamp,
            end_timestamp=target_timestamp,
            include_start=True,
            include_end=True
        )
    )

    if target_preparation_error is not None:
        return target_preparation_error

    if target_df.empty:
        return {
            "status": "unknown",
            "reason": "No reading found at target timestamp"
        }

    target_df, target_validation_error = (
        _validate_meter_reading_values(
            meter_df=target_df,
            context_name="Isolation Forest target reading",
            missing_value_status="unknown"
        )
    )

    if target_validation_error is not None:
        return target_validation_error

    target_reading = float(
        target_df.iloc[0][
            READING_COLUMN
        ]
    )

    # 9. تجهيز features للقراءة المستهدفة

    # لازم يكون عندنا 6 قراءات متتالية مباشرة قبل الهدف
    # حتى rolling features تكون صحيحة زمنيا.
    previous_window = historical_df.tail(
        6
    ).copy()

    if len(previous_window) < 6:
        return {
            "status": "unknown",
            "reason": "Insufficient previous readings for target rolling features",
            "available_samples": len(previous_window),
            "required_samples": 6
        }

    expected_previous_times = pd.date_range(
        end=(
            target_timestamp
            - pd.Timedelta(
                minutes=EXPECTED_INTERVAL_MINUTES
            )
        ),
        periods=6,
        freq=f"{EXPECTED_INTERVAL_MINUTES}min"
    )

    if not previous_window[
        "DateTime"
    ].reset_index(
        drop=True
    ).equals(
        pd.Series(expected_previous_times)
    ):
        return {
            "status": "unknown",
            "reason": (
                "Target rolling window is not temporally continuous "
                "at 30-minute intervals"
            )
        }

    previous_reading = float(
        previous_window[
            READING_COLUMN
        ].iloc[-1]
    )

    # حساب التغير بين القراءة الحالية والقراءة السابقة
    # بنفس الطريقة الي استخدمناها في training data
    target_change_denominator = (
        abs(target_reading)
        + abs(previous_reading)
    )

    if target_change_denominator == 0:
        target_change_pct = 0.0
    else:
        target_change_pct = (
            2
            * (
                target_reading
                - previous_reading
            )
            / target_change_denominator
        )

    # متوسط اخر 6 قراءات قبل القراءة المستهدفة
    target_rolling_mean = previous_window[
        READING_COLUMN
    ].mean()

    # مقدار التذبذب في اخر 6 قراءات قبل القراءة المستهدفة
    target_rolling_std = previous_window[
        READING_COLUMN
    ].std()

    if (
        not np.isfinite(target_rolling_mean)
        or not np.isfinite(target_rolling_std)
    ):
        return {
            "status": "unknown",
            "reason": "Unable to calculate target rolling features"
        }

    # 10. تجهيز القراءة المستهدفة للموديل
    X_target = pd.DataFrame(
        [{
            READING_COLUMN: target_reading,
            "change_pct": target_change_pct,
            "rolling_mean": target_rolling_mean,
            "rolling_std": target_rolling_std
        }],
        columns=feature_columns
    )

    # 11. تشغيل الموديل على القراءة المستهدفة
    prediction = int(
        model.predict(
            X_target
        )[0]
    )

    isolation_score = float(
        model.decision_function(
            X_target
        )[0]
    )

    # Isolation Forest:
    # 1 يعني normal
    # -1 يعني anomaly
    is_anomaly = (
        prediction == -1
    )

    isolation_label = (
        "anomaly"
        if is_anomaly
        else "normal"
    )

    # 12. النتيجة النهائية
    return {
        "status": "success",
        "meter_id": meter_id,
        "target_timestamp": target_timestamp.isoformat(),

        # القراءة الي فحصناها
        "target_reading_kwh": float(
            target_reading
        ),

        # نتيجة Isolation Forest
        "is_anomaly": is_anomaly,
        "isolation_label": isolation_label,

        # هذا score للموديل وليس probability
        "isolation_score": isolation_score,

        # الـ features الخاصة بالقراءة الي فحصناها
        "target_features": {
            "reading_kwh": float(
                target_reading
            ),
            "symmetric_change": float(
                target_change_pct
            ),
            "rolling_mean": float(
                target_rolling_mean
            ),
            "rolling_std": float(
                target_rolling_std
            )
        },

        # عدد القراءات الي تدرب عليها الموديل
        "training_samples": len(
            X_train
        ),

        # اعدادات الموديل عشان نقدر نعيد نفس النتيجة
        "contamination": float(
            contamination
        ),
        "random_state": int(
            random_state
        ),

        # الـ features الي استخدمناها
        "features_used": [
            "reading_kwh",
            "symmetric_change",
            "rolling_mean",
            "rolling_std"
        ],

        "temporal_feature_policy": (
            "rolling features never cross a 30-minute gap"
        ),

        # نسخة الطريقة عشان نقدر نتتبع النتائج لاحقا
        "method_version": "isolation_forest_v3"
    }


def analyze_weather_alignment(
    weather_df,
    target_timestamp,
    actual_kwh,
    expected_kwh,
    timestamp_col="DateTime",
    temperature_col="temperature_c",
    alignment_tolerance_minutes=60,
    hot_threshold_c=30.0,
    cold_threshold_c=10.0,
    usage_deviation_threshold=0.20,
    weather_mode="retrospective"
):
    """
    تحليل ما اذا كان سياق الطقس متوافق مع التغير الموجود
    في استهلاك الطاقة وقت الحدث.

    النتيجة تعتبر weather evidence فقط
    وليست اثبات ان الطقس هو سبب الـ anomaly.

    weather_mode:
    - retrospective: يسمح بقراءة قبل او بعد الحدث،
      وعند التعادل يفضل القراءة السابقة.
    - online: لا يسمح باستخدام قراءة مستقبلية.
    """

    # 1. التحقق من البيانات

    if weather_df is None or weather_df.empty:
        return {
            "status": "unknown",
            "reason": "Weather data is unavailable"
        }

    if target_timestamp is None:
        return {
            "status": "error",
            "reason": "target_timestamp is required"
        }

    if actual_kwh is None or expected_kwh is None:
        return {
            "status": "unknown",
            "reason": "Actual and expected usage are required for weather alignment"
        }

    if weather_mode not in {
        "retrospective",
        "online"
    }:
        return {
            "status": "error",
            "reason": (
                "weather_mode must be either "
                "'retrospective' or 'online'"
            )
        }

    numeric_config = {
        "alignment_tolerance_minutes": alignment_tolerance_minutes,
        "hot_threshold_c": hot_threshold_c,
        "cold_threshold_c": cold_threshold_c,
        "usage_deviation_threshold": usage_deviation_threshold
    }

    for input_name, input_value in numeric_config.items():
        error = _validate_finite_number(
            input_value,
            input_name
        )

        if error is not None:
            return error

    if alignment_tolerance_minutes < 0:
        return {
            "status": "error",
            "reason": "alignment_tolerance_minutes cannot be negative"
        }

    if usage_deviation_threshold < 0:
        return {
            "status": "error",
            "reason": "usage_deviation_threshold cannot be negative"
        }

    if cold_threshold_c >= hot_threshold_c:
        return {
            "status": "error",
            "reason": "cold_threshold_c must be lower than hot_threshold_c"
        }

    # actual / expected لازم يكونوا finite.
    for input_name, input_value in {
        "actual_kwh": actual_kwh,
        "expected_kwh": expected_kwh
    }.items():
        error = _validate_finite_number(
            input_value,
            input_name,
            minimum=0.0
        )

        if error is not None:
            return error

    # 2. التأكد من وجود الاعمدة المطلوبة
    required_columns = {
        timestamp_col,
        temperature_col
    }

    missing_columns = required_columns - set(
        weather_df.columns
    )

    if missing_columns:
        return {
            "status": "error",
            "reason": f"Missing weather columns: {sorted(missing_columns)}"
        }

    # 3. تجهيز الوقت والبيانات
    target_timestamp = _parse_utc_timestamp(
        target_timestamp
    )

    if pd.isna(target_timestamp):
        return {
            "status": "error",
            "reason": "Invalid target_timestamp"
        }

    weather = weather_df.copy()

    weather[timestamp_col] = pd.to_datetime(
        weather[timestamp_col],
        errors="coerce",
        utc=True
    )

    weather[temperature_col] = pd.to_numeric(
        weather[temperature_col],
        errors="coerce"
    )

    numeric_temperature = weather[
        temperature_col
    ].astype(float)

    invalid_weather_rows = (
        weather[timestamp_col].isna()
        | weather[temperature_col].isna()
        | ~np.isfinite(
            numeric_temperature
        )
    )

    # Weather source ممكن يحتوي invalid observations.
    # بنستبعدهم بشكل صريح ونرجع عددهم كـ limitation metadata.
    discarded_weather_rows = int(
        invalid_weather_rows.sum()
    )

    weather = weather[
        ~invalid_weather_rows
    ].copy()

    if weather.empty:
        return {
            "status": "unknown",
            "reason": "No valid weather observations are available"
        }

    if weather[
        timestamp_col
    ].duplicated().any():
        return {
            "status": "error",
            "reason": "Duplicate weather timestamps are ambiguous"
        }

    # online mode ما بسمح للـ agent يشوف قراءة طقس من المستقبل.
    if weather_mode == "online":
        weather = weather[
            weather[timestamp_col] <= target_timestamp
        ].copy()

        if weather.empty:
            return {
                "status": "unknown",
                "reason": (
                    "No weather observation was available "
                    "at or before the target timestamp"
                )
            }

    # 4. جلب اقرب قراءة طقس لوقت الحدث
    weather["time_difference"] = (
        weather[timestamp_col]
        - target_timestamp
    ).abs()

    weather["_is_future"] = (
        weather[timestamp_col]
        > target_timestamp
    )

    # Tie policy:
    # اذا عندنا مثلا 13:00 و 14:00 والهدف 13:30،
    # نختار 13:00 بشكل deterministic.
    weather = weather.sort_values(
        by=[
            "time_difference",
            "_is_future",
            timestamp_col
        ],
        ascending=[
            True,
            True,
            False
        ]
    )

    nearest_weather = weather.iloc[
        0
    ]

    time_difference_minutes = (
        nearest_weather[
            "time_difference"
        ].total_seconds()
        / 60.0
    )

    if (
        time_difference_minutes
        > alignment_tolerance_minutes
    ):
        return {
            "status": "unknown",
            "reason": "No weather observation close enough to target timestamp",
            "nearest_weather_minutes": float(
                time_difference_minutes
            ),
            "alignment_tolerance_minutes": float(
                alignment_tolerance_minutes
            )
        }

    # 5. حساب تغير الاستهلاك عن المتوقع
    actual_kwh = float(
        actual_kwh
    )
    expected_kwh = float(
        expected_kwh
    )

    # اذا الاستهلاك المتوقع صفر
    # ما بنقدر نحسب نسبة التغير بطريقة عادية
    if expected_kwh <= 0:
        return {
            "status": "unknown",
            "reason": "Expected usage must be greater than zero for weather alignment"
        }

    usage_deviation_fraction = (
        actual_kwh
        - expected_kwh
    ) / expected_kwh

    temperature_c = float(
        nearest_weather[
            temperature_col
        ]
    )

    # 6. تحديد نوع سياق الطقس
    if temperature_c >= hot_threshold_c:
        weather_condition = "hot"

    elif temperature_c <= cold_threshold_c:
        weather_condition = "cold"

    else:
        weather_condition = "moderate"

    # 7. تحديد اذا كان الطقس يدعم تفسير التغير
    usage_increase = (
        usage_deviation_fraction
        >= usage_deviation_threshold
    )

    extreme_temperature = (
        temperature_c >= hot_threshold_c
        or temperature_c <= cold_threshold_c
    )

    # ارتفاع الاستهلاك مع حرارة او برودة شديدة
    # يعتبر supporting evidence فقط
    weather_evidence = (
    "supporting"
    if usage_increase and extreme_temperature
    else "not_supporting"
)

    # 8. تحديد confidence بسيط لجودة محاذاة الوقت
    # هذا ليس causal confidence.
    temporal_alignment_score = (
    1.0
    if time_difference_minutes <= 30
    else 0.8
)

    # 9. النتيجة النهائية
    return {
        "status": "success",
        "target_timestamp": target_timestamp.isoformat(),

        # وقت اقرب قراءة طقس تم استخدامها
        "weather_timestamp": nearest_weather[
            timestamp_col
        ].isoformat(),

        # درجة الحرارة وقت الحدث
        "temperature_c": temperature_c,

        # حالة الطقس بشكل مبسط
        "weather_condition": weather_condition,

        # الاستهلاك الفعلي والمتوقع
        "actual_kwh": actual_kwh,
        "expected_kwh": expected_kwh,

        # مقدار تغير الاستهلاك عن المتوقع
        # الاسم fraction لان 0.20 يعني 20%.
        "usage_deviation_fraction": float(
            usage_deviation_fraction
        ),

        # هل الطقس يدعم التفسير او لا
        "weather_evidence": weather_evidence,

        # الفرق الزمني بين الحدث وقراءة الطقس
        "alignment_minutes": float(
            time_difference_minutes
        ),

        # confidence خاص بجودة محاذاة الوقت
        # وليس confidence بأن الطقس هو السبب.
        "temporal_alignment_score": (
            temporal_alignment_score
        ),

        # الحدود المستخدمة في التحليل
        "thresholds": {
            "hot_threshold_c": float(
                hot_threshold_c
            ),
            "cold_threshold_c": float(
                cold_threshold_c
            ),
            "usage_deviation_threshold": float(
                usage_deviation_threshold
            ),
            "alignment_tolerance_minutes": float(
                alignment_tolerance_minutes
            )
        },

        "data_policy": {
            "weather_mode": weather_mode,
            "tie_breaker": "prefer_past_observation",
            "discarded_invalid_weather_rows": discarded_weather_rows
        },

        # تذكير مهم ان النتيجة evidence وليست proof
        "limitation": (
            "Weather alignment is supporting context only "
            "and does not prove that weather caused the usage change."
        ),

        # نسخة الطريقة حتى نقدر نتتبع النتائج
        "method_version": "weather_alignment_v2"
    }

def _normalize_optional_bool(value):
    """
    يحول قيم metadata المنطقية إلى Python bool أو None.

    مهم:
    - np.bool_ -> Python bool
    - NaN / pd.NA -> None
    - القيم غير المعروفة لا يتم تفسيرها كـ True بالخطأ
    """

    if value is None:
        return None

    if isinstance(
        value,
        (bool, np.bool_)
    ):
        return bool(
            value
        )

    try:
        if pd.isna(
            value
        ):
            return None
    except (TypeError, ValueError):
        return None

    # نقبل 0/1 فقط إذا جاءت metadata بهذا الشكل
    if (
        isinstance(
            value,
            (int, np.integer)
        )
        and value in (0, 1)
    ):
        return bool(
            value
        )

    # أي قيمة غامضة مثل "yes" أو "unknown"
    # ما بنحولها إلى True بشكل تلقائي
    return None


def analyze_customer_der_context(
    readings_df,
    customer_metadata_df,
    meter_id,
    target_timestamp,
    actual_kwh,
    expected_kwh,
    lookback_intervals=4,
    expected_interval_minutes=30,
    persistence_threshold=0.20,
    persistence_min_prior_intervals=2,
    persistence_expected_kwh_by_timestamp=None
):
    """
    تحليل سياق العميل و DER مثل EV و Solar
    لمعرفة اذا كانت البيانات تدعم احد التفسيرات المحتملة.

    النتيجة تعتبر evidence فقط
    وليست اثبات للسبب الحقيقي للـ anomaly.

    persistence_expected_kwh_by_timestamp اختيارية.
    اذا توفرت نستخدم expected مستقل لكل interval سابق.
    اذا لم تتوفر نستخدم expected الخاص بالهدف كـ constant reference
    ونصرح بهذا الافتراض في النتيجة بدل ما نسميه seasonal baseline.
    """

    # 1. التحقق من البيانات
    if readings_df is None or readings_df.empty:
        return {
            "status": "error",
            "reason": "readings_df is required"
        }

    if (
        customer_metadata_df is None
        or customer_metadata_df.empty
    ):
        return {
            "status": "unknown",
            "reason": "Customer metadata is unavailable"
        }

    if not meter_id or not str(
        meter_id
    ).strip():
        return {
            "status": "error",
            "reason": "meter_id is required"
        }

    if target_timestamp is None:
        return {
            "status": "error",
            "reason": "target_timestamp is required"
        }

    if (
        actual_kwh is None
        or expected_kwh is None
    ):
        return {
            "status": "unknown",
            "reason": "Actual and expected usage are required"
        }

    integer_settings = {
        "lookback_intervals": lookback_intervals,
        "expected_interval_minutes": expected_interval_minutes,
        "persistence_min_prior_intervals": persistence_min_prior_intervals
    }

    for input_name, input_value in integer_settings.items():
        if (
            isinstance(input_value, bool)
            or not isinstance(
                input_value,
                (int, np.integer)
            )
            or input_value < 1
        ):
            return {
                "status": "error",
                "reason": f"{input_name} must be a positive integer"
            }

    if (
        persistence_min_prior_intervals
        > lookback_intervals
    ):
        return {
            "status": "error",
            "reason": (
                "persistence_min_prior_intervals cannot exceed "
                "lookback_intervals"
            )
        }

    persistence_threshold_error = _validate_finite_number(
        persistence_threshold,
        "persistence_threshold",
        minimum=0.0
    )

    if persistence_threshold_error is not None:
        return persistence_threshold_error

    for input_name, input_value in {
        "actual_kwh": actual_kwh,
        "expected_kwh": expected_kwh
    }.items():
        error = _validate_finite_number(
            input_value,
            input_name,
            minimum=0.0
        )

        if error is not None:
            return error

    # 2. تجهيز الوقت
    target_timestamp = _parse_utc_timestamp(
        target_timestamp
    )

    if pd.isna(
        target_timestamp
    ):
        return {
            "status": "error",
            "reason": "Invalid target_timestamp"
        }

    # تجهيز expected references للـ persistence اذا وصلت من caller.
    # Day 6 wrapper يقدر يمرر baseline خاص بكل interval.
    expected_lookup = None
    persistence_reference_mode = "target_expected_constant"

    if persistence_expected_kwh_by_timestamp is not None:
        if not isinstance(
            persistence_expected_kwh_by_timestamp,
            dict
        ):
            return {
                "status": "error",
                "reason": (
                    "persistence_expected_kwh_by_timestamp "
                    "must be a dictionary"
                )
            }

        expected_lookup = {}

        for raw_timestamp, raw_expected in (
            persistence_expected_kwh_by_timestamp.items()
        ):
            parsed_expected_timestamp = _parse_utc_timestamp(
                raw_timestamp
            )

            if pd.isna(parsed_expected_timestamp):
                return {
                    "status": "error",
                    "reason": (
                        "persistence expected references contain "
                        "an invalid timestamp"
                    )
                }

            expected_error = _validate_finite_number(
                raw_expected,
                "persistence expected_kwh",
                minimum=0.0000001
            )

            if expected_error is not None:
                return expected_error

            expected_lookup[
                parsed_expected_timestamp
            ] = float(raw_expected)

        persistence_reference_mode = "per_interval_expected_kwh"

    # 3. جلب metadata الخاصة بالعداد
    if "Meter_ID" not in customer_metadata_df.columns:
        return {
            "status": "error",
            "reason": "Customer metadata must contain Meter_ID"
        }

    meter_metadata = customer_metadata_df[
        customer_metadata_df["Meter_ID"] == meter_id
    ]

    if meter_metadata.empty:
        return {
            "status": "unknown",
            "reason": "No customer metadata found for meter"
        }

    if len(
        meter_metadata
    ) > 1:
        return {
            "status": "error",
            "reason": "Multiple customer metadata rows found for meter"
        }

    metadata = meter_metadata.iloc[
        0
    ]

    has_ev = _normalize_optional_bool(
        metadata.get(
            "Has_EV",
            None
        )
    )

    has_solar = _normalize_optional_bool(
        metadata.get(
            "Has_Solar",
            None
        )
    )

    # 4. تجهيز قراءات الـ persistence المطلوبة فقط

    # DER هنا لا تحتاج كل تاريخ العداد.
    # تحتاج فقط intervals السابقة مباشرة للحدث.
    # actual_kwh الخاصة بالهدف وصلت كـ input منفصل،
    # لذلك target/future rows ما لازم تغير نتيجة السياق.
    persistence_window_start = (
        target_timestamp
        - pd.Timedelta(
            minutes=(
                expected_interval_minutes
                * lookback_intervals
            )
        )
    )

    meter_df, preparation_error = (
        _prepare_meter_time_window(
            readings_df=readings_df,
            meter_id=meter_id,
            start_timestamp=persistence_window_start,
            end_timestamp=target_timestamp,
            include_start=True,
            include_end=False
        )
    )

    if preparation_error is not None:
        return preparation_error

    meter_df, validation_error = (
        _validate_meter_reading_values(
            meter_df=meter_df,
            context_name="DER persistence window"
        )
    )

    if validation_error is not None:
        return validation_error

    # 5. حساب مقدار التغير عن المتوقع
    actual_kwh = float(
        actual_kwh
    )
    expected_kwh = float(
        expected_kwh
    )

    if expected_kwh <= 0:
        usage_deviation_fraction = None
    else:
        usage_deviation_fraction = (
            actual_kwh
            - expected_kwh
        ) / expected_kwh

    # 6. جلب القراءات السابقة للحدث
    #
    # persistence الحقيقي لازم يعني ان نفس اتجاه التغير
    # استمر في intervals متتالية مباشرة قبل الحدث.
    # ما بنقارن current reading بمتوسط التاريخ فقط.
    previous_readings = meter_df.tail(
        lookback_intervals
    )

    current_direction = 0

    if usage_deviation_fraction is not None:
        if (
            usage_deviation_fraction
            >= persistence_threshold
        ):
            current_direction = 1

        elif (
            usage_deviation_fraction
            <= -persistence_threshold
        ):
            current_direction = -1

    consecutive_prior_deviations = []
    consecutive_prior_count = 0
    continuity_broken = False
    expected_reference_missing = False

    if (
        expected_kwh > 0
        and current_direction != 0
    ):
        readings_by_time = meter_df.set_index(
            "DateTime"
        )

        for offset in range(
            1,
            lookback_intervals + 1
        ):
            expected_time = (
                target_timestamp
                - pd.Timedelta(
                    minutes=(
                        expected_interval_minutes
                        * offset
                    )
                )
            )

            if expected_time not in readings_by_time.index:
                continuity_broken = True
                break

            prior_value = float(
                readings_by_time.loc[
                    expected_time,
                    READING_COLUMN
                ]
            )

            if expected_lookup is None:
                prior_expected_kwh = expected_kwh
            else:
                prior_expected_kwh = expected_lookup.get(
                    expected_time
                )

                if prior_expected_kwh is None:
                    expected_reference_missing = True
                    break

            prior_deviation = (
                prior_value
                - prior_expected_kwh
            ) / prior_expected_kwh

            same_direction = (
                (
                    current_direction == 1
                    and prior_deviation
                    >= persistence_threshold
                )
                or (
                    current_direction == -1
                    and prior_deviation
                    <= -persistence_threshold
                )
            )

            if not same_direction:
                break

            consecutive_prior_count += 1

            consecutive_prior_deviations.append(
                float(
                    prior_deviation
                )
            )

    persistence = (
        current_direction != 0
        and not expected_reference_missing
        and consecutive_prior_count
        >= persistence_min_prior_intervals
    )

    target_hour = target_timestamp.hour

    persistence_limitation = None

    if persistence_reference_mode == "target_expected_constant":
        persistence_limitation = (
            "Prior intervals were compared with the target interval's "
            "expected_kwh because per-interval expectations were not supplied."
        )
    elif expected_reference_missing:
        persistence_limitation = (
            "Per-interval expected usage was missing for at least one "
            "required prior interval, so persistence was not established."
        )

    # 7. تحليل EV
    ev_reasons = []
    ev_limitations = []

    if has_ev is None:
        ev_evidence = "unknown"

        ev_limitations.append(
            "EV ownership metadata is unavailable"
        )

    elif has_ev is False:
        ev_evidence = "not_supporting"

        ev_reasons.append(
            "Customer metadata does not indicate an EV"
        )

    else:
        # وجود EV في metadata هو evidence سياقي فقط
        ev_reasons.append(
            "Customer metadata indicates an EV is registered"
        )

        evening_event = (
            17 <= target_hour <= 23
        )

        usage_increase = (
            usage_deviation_fraction is not None
            and usage_deviation_fraction >= 0.20
        )

        if evening_event:
            ev_reasons.append(
                "Usage increase occurs during evening hours"
            )

        if usage_increase:
            ev_reasons.append(
                "Usage is meaningfully above expected consumption"
            )

        if persistence:
            ev_reasons.append(
                "Load change persists across consecutive recent intervals"
            )

        ev_evidence = (
            "supporting"
            if (
                evening_event
                and usage_increase
                and persistence
            )
            else "not_supporting"
        )

        ev_limitations.append(
            "The pattern is not unique to EV charging"
        )

        ev_limitations.append(
            "HVAC, water heating, appliances, or customer behavior may produce similar load"
        )

        if persistence_limitation is not None:
            ev_limitations.append(
                persistence_limitation
            )

    # 8. تحليل Solar
    solar_reasons = []
    solar_limitations = []

    if has_solar is None:
        solar_evidence = "unknown"

        solar_limitations.append(
            "Solar metadata is unavailable"
        )

    elif has_solar is False:
        solar_evidence = "not_supporting"

        solar_reasons.append(
            "Customer metadata does not indicate solar generation"
        )

    else:

        solar_reasons.append(
            "Customer metadata indicates solar generation is present"
        )

        midday_event = (
            10 <= target_hour <= 15
        )

        usage_drop = (
            usage_deviation_fraction is not None
            and usage_deviation_fraction <= -0.20
        )

        if midday_event:
            solar_reasons.append(
                "Usage drop occurs during daylight hours"
            )

        if usage_drop:
            solar_reasons.append(
                "Net load is meaningfully below expected consumption"
            )

        solar_evidence = (
            "supporting"
            if midday_event and usage_drop
            else "not_supporting"
        )

        solar_limitations.append(
            "A daytime load drop does not prove solar generation caused the change"
        )

        solar_limitations.append(
            "Occupancy or appliance changes may create similar patterns"
        )

    # 9. تحليل تغير سلوك العميل
    behavior_reasons = []
    behavior_limitations = []

    if (
        usage_deviation_fraction is not None
        and abs(
            usage_deviation_fraction
        ) >= 0.20
        and persistence
    ):
        behavior_evidence = "supporting"

        behavior_reasons.append(
            "Usage differs meaningfully from expected consumption"
        )

        behavior_reasons.append(
            "The change persists across consecutive recent intervals"
        )

    else:
        behavior_evidence = "not_supporting"

    behavior_limitations.append(
        "Consumption patterns alone cannot identify the exact customer activity"
    )

    if continuity_broken:
        behavior_limitations.append(
            "A missing interval interrupted the persistence check"
        )

    if persistence_limitation is not None:
        behavior_limitations.append(
            persistence_limitation
        )

    # 10. النتيجة النهائية
    return {
        "status": "success",
        "meter_id": meter_id,
        "target_timestamp": target_timestamp.isoformat(),

        "actual_kwh": actual_kwh,
        "expected_kwh": expected_kwh,

        # الاسم fraction لان 0.20 يعني 20%.
        "usage_deviation_fraction": (
            usage_deviation_fraction
        ),

        "persistence": {
            "is_persistent": bool(
                persistence
            ),
            "direction": (
                "increase"
                if current_direction == 1
                else "decrease"
                if current_direction == -1
                else "not_significant"
            ),
            "consecutive_prior_intervals": int(
                consecutive_prior_count
            ),
            "required_prior_intervals": int(
                persistence_min_prior_intervals
            ),
            "lookback_intervals": int(
                lookback_intervals
            ),
            "expected_interval_minutes": int(
                expected_interval_minutes
            ),
            "threshold_fraction": float(
                persistence_threshold
            ),
            "continuity_broken": bool(
                continuity_broken
            ),
            "previous_readings_available": int(
                len(
                    previous_readings
                )
            ),
            "prior_deviations": [
                float(value)
                for value
                in consecutive_prior_deviations
            ],
            "expected_reference_mode": (
                persistence_reference_mode
            ),
            "expected_reference_missing": bool(
                expected_reference_missing
            ),
            "limitation": persistence_limitation
        },

        "ev": {
            "has_ev": has_ev,
            "evidence": ev_evidence,
            "reasons": ev_reasons,
            "limitations": ev_limitations
        },

        "solar": {
            "has_solar": has_solar,
            "evidence": solar_evidence,
            "reasons": solar_reasons,
            "limitations": solar_limitations
        },

        "customer_behavior": {
            "evidence": behavior_evidence,
            "reasons": behavior_reasons,
            "limitations": behavior_limitations
        },

        "limitation": (
            "DER and customer-context analysis provides supporting evidence only "
            "and does not prove the cause of an anomaly."
        ),

        "method_version": "customer_der_context_v3"
    }


def adapt_day3_anomaly_event_for_severity(
    anomaly_type,
    deviation_pct=None,
    event_severity=None,
):
    """
    يحول عقد Day 3 الجديد إلى inputs واضحة لمحرك Severity في Day 5.

    Day 3:
        deviation_pct = 80.0 يعني 80%

    Day 5:
        usage_deviation_fraction = 0.80 يعني 80%

    منع الـ double counting:
    - drop / spike / zero_period:
      magnitude evidence -> usage component فقط

    - gap / flatline:
      structural evidence -> rule-based component فقط
    """

    supported_types = {
        "drop",
        "spike",
        "zero_period",
        "gap",
        "flatline",
    }

    if anomaly_type not in supported_types:
        return {
            "status": "error",
            "reason": f"Unsupported anomaly_type: {anomaly_type}",
        }

    magnitude_types = {
        "drop",
        "spike",
        "zero_period",
    }

    # ---------------------------------------------------------
    # Magnitude anomalies
    # ---------------------------------------------------------
    if anomaly_type in magnitude_types:

        if deviation_pct is None:
            return {
                "status": "unknown",
                "reason": "deviation_pct is required for magnitude anomaly",
            }

        if (
            isinstance(deviation_pct, bool)
            or not isinstance(
                deviation_pct,
                (int, float, np.number)
            )
        ):
            return {
                "status": "error",
                "reason": "deviation_pct must be numeric",
            }

        if (
            not np.isfinite(deviation_pct)
            or deviation_pct < 0
        ):
            return {
                "status": "error",
                "reason": (
                    "deviation_pct must be a finite "
                    "non-negative percentage"
                ),
            }

        deviation_fraction = (
            float(deviation_pct) / 100.0
        )

        # Day 3 deviation_pct هو magnitude موجب.
        # الاتجاه موجود في anomaly_type.
        if anomaly_type in {
            "drop",
            "zero_period",
        }:
            deviation_fraction = -deviation_fraction

        return {
            "status": "success",
            "anomaly_type": anomaly_type,

            # القيمة الأصلية من Day 3
            "source_deviation_pct": float(
                deviation_pct
            ),

            # القيمة التي يستخدمها Day 5
            "usage_deviation_fraction": (
                deviation_fraction
            ),

            # صفر لمنع حساب نفس evidence مرتين
            "rule_based_score": 0.0,

            "evidence_ownership": (
                "usage_deviation"
            ),

            "method_version": (
                "day3_anomaly_adapter_v1"
            ),
        }

    # ---------------------------------------------------------
    # Structural anomalies: gap / flatline
    # ---------------------------------------------------------

    if event_severity is None:
        return {
            "status": "unknown",
            "reason": (
                "event_severity is required "
                "for structural anomaly"
            ),
        }

    if (
        isinstance(event_severity, bool)
        or not isinstance(
            event_severity,
            (int, float, np.number)
        )
    ):
        return {
            "status": "error",
            "reason": "event_severity must be numeric",
        }

    if (
        not np.isfinite(event_severity)
        or not 0 <= float(event_severity) <= 100
    ):
        return {
            "status": "error",
            "reason": (
                "event_severity must be "
                "between 0 and 100"
            ),
        }

    return {
        "status": "success",
        "anomaly_type": anomaly_type,

        # لا يوجد usage magnitude صالح لهذا النوع
        "usage_deviation_fraction": 0.0,

        "rule_based_score": float(
            event_severity
        ),

        "evidence_ownership": (
            "structural_rule"
        ),

        "method_version": (
            "day3_anomaly_adapter_v1"
        ),
    }


def adapt_day4_peer_comparison_for_severity(
    peer_deviation_pct,
):
    """
    يحول signed percentage من Day 4 peer comparison
    إلى anomaly-strength score بين 0 و 1.

    -80% -> 0.80
    +40% -> 0.40
    +150% -> 1.00

    الاتجاه يبقى محفوظ كـ metadata،
    لكن Severity تستخدم magnitude.
    """

    if peer_deviation_pct is None:
        return {
            "status": "unknown",
            "reason": "peer_deviation_pct is unavailable",
            "peer_deviation_score": None,
        }

    if (
        isinstance(peer_deviation_pct, bool)
        or not isinstance(
            peer_deviation_pct,
            (int, float, np.number)
        )
    ):
        return {
            "status": "error",
            "reason": "peer_deviation_pct must be numeric",
            "peer_deviation_score": None,
        }

    if not np.isfinite(peer_deviation_pct):
        return {
            "status": "error",
            "reason": (
                "peer_deviation_pct must be finite"
            ),
            "peer_deviation_score": None,
        }

    peer_deviation_pct = float(
        peer_deviation_pct
    )

    peer_deviation_score = min(
        abs(peer_deviation_pct) / 100.0,
        1.0,
    )

    if peer_deviation_pct < 0:
        direction = "below_peers"
    elif peer_deviation_pct > 0:
        direction = "above_peers"
    else:
        direction = "aligned_with_peers"

    return {
        "status": "success",

        # Signed percentage الأصلي من Day 4
        "source_deviation_pct": (
            peer_deviation_pct
        ),

        # 0..1 magnitude لمحرك Severity
        "peer_deviation_score": (
            peer_deviation_score
        ),

        "direction": direction,

        "method_version": (
            "day4_peer_adapter_v1"
        ),
    }

def adapt_day3_quality_for_day5(
    quality_score,
    reliable,
    quality_status=None
):
    """
    Adapter واضح بين Day 3 quality contract و Day 5.

    Day 3:
        quality_score = 0..100
        reliable = bool

    Day 5:
        triage.data_confidence = 0..1
        revenue.data_reliable = bool
    """

    error = _validate_finite_number(
        quality_score,
        "quality_score",
        minimum=0.0,
        maximum=100.0
    )

    if error is not None:
        return error

    normalized_reliable = _normalize_optional_bool(
        reliable
    )

    if normalized_reliable is None:
        return {
            "status": "unknown",
            "reason": "reliable flag is unavailable or invalid"
        }

    return {
        "status": "success",
        "quality_status": quality_status,
        "quality_score": float(
            quality_score
        ),
        "data_confidence": float(
            quality_score
        ) / 100.0,
        "data_reliable": normalized_reliable,
        "method_version": "day3_quality_adapter_v1"
    }


def adapt_day4_shared_incident_for_triage(
    shared_status,
    affected_fraction,
    incident_type=None,
    shared_confidence=None
):
    """
    SharedIncident.affected_fraction يملك scope evidence.

    shared_confidence يبقى metadata فقط،
    ولا نستخدمه كـ data_confidence أو upstream score
    حتى ما نعمل double counting.
    """

    if shared_status != "answered":
        return {
            "status": "unknown",
            "reason": "Shared incident output is not answered"
        }

    error = _validate_finite_number(
        affected_fraction,
        "affected_fraction",
        minimum=0.0,
        maximum=1.0
    )

    if error is not None:
        return error

    if shared_confidence is not None:
        confidence_error = _validate_finite_number(
            shared_confidence,
            "shared_confidence",
            minimum=0.0,
            maximum=1.0
        )

        if confidence_error is not None:
            return confidence_error

    return {
        "status": "success",
        "scope_ratio": float(
            affected_fraction
        ),
        "incident_type": incident_type,

        # نخزنها كـ metadata فقط.
        "shared_classification_confidence": (
            float(shared_confidence)
            if shared_confidence is not None
            else None
        ),

        "confidence_usage": (
            "metadata_only_not_triage_data_confidence"
        ),

        "method_version": (
            "day4_shared_triage_adapter_v1"
        )
    }


def adapt_energy_balance_for_triage(
    energy_balance_result,
    excess_difference_reference=0.50
):
    """
    يحول Tool 11 energy-balance evidence إلى upstream evidence score.

    scope يأتي من affected_fraction،
    لذلك upstream evidence هنا يأتي من مصدر مختلف:
    transformer / downstream energy mismatch.
    """

    if not isinstance(
        energy_balance_result,
        dict
    ):
        return {
            "status": "error",
            "reason": "energy_balance_result must be a dictionary"
        }

    if (
        energy_balance_result.get(
            "status"
        ) != "success"
    ):
        return {
            "status": "unknown",
            "reason": "Energy balance result is unavailable"
        }

    reference_error = _validate_finite_number(
        excess_difference_reference,
        "excess_difference_reference",
        minimum=0.000001
    )

    if reference_error is not None:
        return reference_error

    balance_status = energy_balance_result.get(
        "balance_status"
    )

    difference_pct = energy_balance_result.get(
        "difference_pct"
    )

    tolerance_pct = energy_balance_result.get(
        "tolerance_pct"
    )

    if balance_status == "balanced":
        score = 0.0

    elif (
        balance_status == "imbalanced"
        and difference_pct is None
    ):
        # expected transformer = 0 بينما actual > 0
        score = 1.0

    else:
        difference_error = _validate_finite_number(
            difference_pct,
            "difference_pct",
            minimum=0.0
        )

        if difference_error is not None:
            return difference_error

        tolerance_error = _validate_finite_number(
            tolerance_pct,
            "tolerance_pct",
            minimum=0.0
        )

        if tolerance_error is not None:
            return tolerance_error

        excess_difference = max(
            0.0,
            float(difference_pct)
            - float(tolerance_pct)
        )

        score = min(
            excess_difference
            / float(
                excess_difference_reference
            ),
            1.0
        )

    return {
        "status": "success",
        "upstream_evidence_score": float(
            score
        ),
        "source": "energy_balance",
        "excess_difference_reference": float(
            excess_difference_reference
        ),
        "method_version": (
            "energy_balance_triage_adapter_v1"
        )
    }

def calculate_anomaly_severity(
    usage_deviation_fraction,
    rule_based_score,
    isolation_score,
    peer_deviation_score,
):
    """
    حساب Hybrid Anomaly Severity من 0 إلى 100.

    Input contracts:

    usage_deviation_fraction:
        signed fraction.
        -0.80 = 80% drop
        +0.80 = 80% spike

    rule_based_score:
        structural rule evidence from 0 to 100.

    isolation_score:
        raw Isolation Forest decision_function score.
        هذا ليس probability.

    peer_deviation_score:
        normalized peer abnormality from 0 to 1.

    مهم:
    Evidence ownership يتم قبل هذه function لمنع
    حساب نفس anomaly evidence أكثر من مرة.
    """

    # ---------------------------------------------------------
    # 1. Required inputs
    # ---------------------------------------------------------

    raw_inputs = {
        "usage_deviation_fraction": (
            usage_deviation_fraction
        ),
        "rule_based_score": rule_based_score,
        "isolation_score": isolation_score,
        "peer_deviation_score": (
            peer_deviation_score
        ),
    }

    for input_name, input_value in raw_inputs.items():

        if input_value is None:
            return {
                "status": "unknown",
                "reason": (
                    f"{input_name} is required "
                    "for severity calculation"
                ),
            }

        if (
            isinstance(input_value, bool)
            or not isinstance(
                input_value,
                (int, float, np.number)
            )
        ):
            return {
                "status": "error",
                "reason": (
                    f"{input_name} must be numeric"
                ),
            }

        if not np.isfinite(input_value):
            return {
                "status": "error",
                "reason": (
                    f"{input_name} must be "
                    "a finite numeric value"
                ),
            }

    # ---------------------------------------------------------
    # 2. Contract ranges
    # ---------------------------------------------------------

    if not 0 <= float(rule_based_score) <= 100:
        return {
            "status": "error",
            "reason": (
                "rule_based_score must be "
                "between 0 and 100"
            ),
        }

    if not 0 <= float(peer_deviation_score) <= 1:
        return {
            "status": "error",
            "reason": (
                "peer_deviation_score must be "
                "between 0 and 1"
            ),
        }

    # ---------------------------------------------------------
    # 3. Usage deviation
    # ---------------------------------------------------------

    # Spike و drop كلاهما مهمين للـseverity.
    # القيمة قد تتجاوز 1 في spike أكبر من 100%.
    usage_normalized = min(
        abs(float(usage_deviation_fraction)),
        1.0,
    )

    # ---------------------------------------------------------
    # 4. Structural rule evidence
    # ---------------------------------------------------------

    rule_normalized = (
        float(rule_based_score) / 100.0
    )

    # ---------------------------------------------------------
    # 5. Isolation Forest
    # ---------------------------------------------------------

    # decision_function:
    # negative = stronger anomaly evidence
    # positive = normal side of boundary
    #
    # -0.2 reference ما زال provisional
    # وسيتم فحصه في calibration step.
    if isolation_score >= 0:
        isolation_normalized = 0.0
    else:
        isolation_normalized = min(
            abs(float(isolation_score)) / 0.2,
            1.0,
        )

    # ---------------------------------------------------------
    # 6. Peer abnormality
    # ---------------------------------------------------------

    peer_normalized = float(
        peer_deviation_score
    )

    # ---------------------------------------------------------
    # 7. Contributions
    # ---------------------------------------------------------

    usage_contribution = (
        usage_normalized * 35
    )

    rule_contribution = (
        rule_normalized * 25
    )

    isolation_contribution = (
        isolation_normalized * 20
    )

    peer_contribution = (
        peer_normalized * 20
    )

    # ---------------------------------------------------------
    # 8. Final severity
    # ---------------------------------------------------------

    severity_score = (
        usage_contribution
        + rule_contribution
        + isolation_contribution
        + peer_contribution
    )

    severity_score = round(
        min(max(severity_score, 0.0), 100.0),
        2,
    )

    return {
        "status": "success",

        "severity_score": severity_score,

        "normalized_components": {
            "usage_deviation": round(
                usage_normalized,
                4,
            ),
            "rule_based": round(
                rule_normalized,
                4,
            ),
            "isolation_forest": round(
                isolation_normalized,
                4,
            ),
            "peer_deviation": round(
                peer_normalized,
                4,
            ),
        },

        "contributions": {
            "usage_deviation": round(
                usage_contribution,
                2,
            ),
            "rule_based": round(
                rule_contribution,
                2,
            ),
            "isolation_forest": round(
                isolation_contribution,
                2,
            ),
            "peer_deviation": round(
                peer_contribution,
                2,
            ),
        },

        "weights": {
            "usage_deviation": 35,
            "rule_based": 25,
            "isolation_forest": 20,
            "peer_deviation": 20,
        },

        "calibration": {
            "usage_unit": "fraction",
            "usage_cap": 1.0,

            "rule_based_range": "0-100",

            "isolation_score_type": (
                "decision_function"
            ),
            "isolation_negative_reference": (
                -0.2
            ),

            "peer_deviation_range": "0-1",

            # واضح بدل ما Codex يسأل شو بصير
            # إذا component ناقص.
            "missing_component_policy": (
                "require_all_components"
            ),
        },

        "evidence_ownership": {
            "usage_deviation": (
                "magnitude anomaly evidence"
            ),
            "rule_based": (
                "structural anomaly evidence"
            ),
            "isolation_forest": (
                "model anomaly evidence"
            ),
            "peer_deviation": (
                "target-versus-peer evidence"
            ),
        },

        "method_version": "hybrid_severity_v2",
    }

def build_severity_calibration_report():
    """
    تقرير calibration deterministic لمحرك Severity.

    الهدف ليس اثبات ان الاوزان مثالية،
    بل التأكد من الخصائص الأساسية:
    - normal evidence يبقى منخفض.
    - تقوية component واحد ترفع score بشكل monotonic.
    - كل components عند الحد الاقصى تعطي 100.

    هذا التقرير يغطي component calibration.
    Scenario-level calibration على fixtures الفعلية يظل جزء من integration.
    """

    calibration_cases = {
        "all_zero": {
            "usage_deviation_fraction": 0.0,
            "rule_based_score": 0.0,
            "isolation_score": 0.10,
            "peer_deviation_score": 0.0
        },
        "usage_20_pct": {
            "usage_deviation_fraction": 0.20,
            "rule_based_score": 0.0,
            "isolation_score": 0.10,
            "peer_deviation_score": 0.0
        },
        "usage_80_pct": {
            "usage_deviation_fraction": 0.80,
            "rule_based_score": 0.0,
            "isolation_score": 0.10,
            "peer_deviation_score": 0.0
        },
        "isolation_mild": {
            "usage_deviation_fraction": 0.0,
            "rule_based_score": 0.0,
            "isolation_score": -0.02,
            "peer_deviation_score": 0.0
        },
        "isolation_strong": {
            "usage_deviation_fraction": 0.0,
            "rule_based_score": 0.0,
            "isolation_score": -0.15,
            "peer_deviation_score": 0.0
        },
        "peer_20_pct": {
            "usage_deviation_fraction": 0.0,
            "rule_based_score": 0.0,
            "isolation_score": 0.10,
            "peer_deviation_score": 0.20
        },
        "peer_80_pct": {
            "usage_deviation_fraction": 0.0,
            "rule_based_score": 0.0,
            "isolation_score": 0.10,
            "peer_deviation_score": 0.80
        },
        "all_max": {
            "usage_deviation_fraction": 1.0,
            "rule_based_score": 100.0,
            "isolation_score": -0.50,
            "peer_deviation_score": 1.0
        }
    }

    scores = {}

    for case_name, case_inputs in calibration_cases.items():
        result = calculate_anomaly_severity(
            **case_inputs
        )

        if result["status"] != "success":
            return {
                "status": "error",
                "reason": (
                    f"Calibration case failed: "
                    f"{case_name}"
                ),
                "case_result": result
            }

        scores[
            case_name
        ] = result[
            "severity_score"
        ]

    checks = {
        "all_zero_is_zero": (
            scores[
                "all_zero"
            ] == 0.0
        ),
        "usage_is_monotonic": (
            scores[
                "usage_80_pct"
            ]
            > scores[
                "usage_20_pct"
            ]
        ),
        "isolation_is_monotonic": (
            scores[
                "isolation_strong"
            ]
            > scores[
                "isolation_mild"
            ]
        ),
        "peer_is_monotonic": (
            scores[
                "peer_80_pct"
            ]
            > scores[
                "peer_20_pct"
            ]
        ),
        "all_max_is_100": (
            scores[
                "all_max"
            ] == 100.0
        )
    }

    return {
        "status": (
            "success"
            if all(
                checks.values()
            )
            else "warning"
        ),
        "scores": scores,
        "checks": checks,
        "weights": {
            "usage_deviation": 35,
            "rule_based": 25,
            "isolation_forest": 20,
            "peer_deviation": 20
        },
        "limitations": [
            (
                "This is deterministic component calibration, "
                "not a claim that the weights are statistically optimal."
            ),
            (
                "Scenario-level calibration should continue on the "
                "published project fixtures during integration."
            )
        ],
        "method_version": (
            "severity_calibration_report_v1"
        )
    }

def estimate_revenue_at_risk(
    expected_kwh,
    reliable_observed_kwh,
    tariff_jod_per_kwh,
    tariff_version=None,
    recoverability_base=0.80,
    recoverability_low=0.60,
    recoverability_high=1.00,
    data_reliable=None,
    calculation_timestamp=None,
    calculation_window_start=None,
    calculation_window_end=None,
    tariff_source=None,
    tariff_bracket_name=None,
    forecast_reference=None,
    observed_reference=None,
    quality_reference=None,
    quality_score=None,
    exclusions=None
):
    """
    تقدير Revenue at Risk بالدينار الاردني.

    يتم حساب الطاقة المفقودة المتوقعة،
    ثم تطبيق التعرفة ونسب recoverability
    لاخراج low / base / high estimates.

    قيم recoverability هي sensitivity assumptions
    وليست thresholds لشدة الخسارة.

    مهم:
    tariff_jod_per_kwh هنا rate تم حلها مسبقا من caller.
    اذا التعرفة bracketed، caller لازم يمرر اسم الـ bracket
    والسورس المستخدم. fixed charges غير محسوبة هنا.
    """

    # ---------------------------------------------------------
    # 1. التحقق من بيانات الطاقة
    # ---------------------------------------------------------

    numeric_inputs = {
        "expected_kwh": expected_kwh,
        "reliable_observed_kwh": reliable_observed_kwh
    }

    for input_name, input_value in numeric_inputs.items():

        error = _validate_finite_number(
            input_value,
            input_name,
            minimum=0.0
        )

        if error is not None:
            return error

    # ---------------------------------------------------------
    # 2. التحقق من موثوقية البيانات
    # ---------------------------------------------------------

    normalized_reliable = _normalize_optional_bool(
        data_reliable
    )

    if normalized_reliable is not True:
        return {
            "status": "unknown",
            "reason": "Observed usage is not reliable enough for revenue estimation"
        }

    if quality_score is not None:
        quality_error = _validate_finite_number(
            quality_score,
            "quality_score",
            minimum=0.0,
            maximum=100.0
        )

        if quality_error is not None:
            return quality_error

    # ---------------------------------------------------------
    # 3. التحقق من التعرفة
    # ---------------------------------------------------------

    # Missing tariff لازم يعطي Unknown وليس صفر
    if tariff_jod_per_kwh is None:
        return {
            "status": "unknown",
            "reason": "Tariff is unavailable",
            "expected_missing_kwh": None,
            "revenue_at_risk_jod": None
        }

    tariff_error = _validate_finite_number(
        tariff_jod_per_kwh,
        "tariff_jod_per_kwh",
        minimum=0.0
    )

    if tariff_error is not None:
        return tariff_error

    # tariff version والسورس جزء من audit trail.
    if (
        tariff_version is None
        or not str(
            tariff_version
        ).strip()
    ):
        return {
            "status": "unknown",
            "reason": "tariff_version is required for auditable revenue estimation",
            "revenue_at_risk_jod": None
        }

    if (
        tariff_source is None
        or not str(
            tariff_source
        ).strip()
    ):
        return {
            "status": "unknown",
            "reason": "tariff_source is required for auditable revenue estimation",
            "revenue_at_risk_jod": None
        }

    # ---------------------------------------------------------
    # 4. التحقق من Recoverability assumptions
    # ---------------------------------------------------------

    recoverability_inputs = {
        "recoverability_low": recoverability_low,
        "recoverability_base": recoverability_base,
        "recoverability_high": recoverability_high
    }

    for input_name, input_value in recoverability_inputs.items():

        error = _validate_finite_number(
            input_value,
            input_name,
            minimum=0.0,
            maximum=1.0
        )

        if error is not None:
            return error

    # لازم Low <= Base <= High
    if not (
        recoverability_low
        <= recoverability_base
        <= recoverability_high
    ):
        return {
            "status": "error",
            "reason": (
                "Recoverability assumptions must satisfy "
                "low <= base <= high"
            )
        }

    # ---------------------------------------------------------
    # 5. تجهيز وقت الحساب والـ provenance
    # ---------------------------------------------------------

    provenance_fields = {
        "calculation_timestamp": calculation_timestamp,
        "calculation_window_start": calculation_window_start,
        "calculation_window_end": calculation_window_end,
        "forecast_reference": forecast_reference,
        "observed_reference": observed_reference,
        "quality_reference": quality_reference
    }

    missing_provenance = [
        input_name
        for input_name, input_value
        in provenance_fields.items()
        if (
            input_value is None
            or (
                isinstance(
                    input_value,
                    str
                )
                and not input_value.strip()
            )
        )
    ]

    if missing_provenance:
        return {
            "status": "unknown",
            "reason": (
                "Revenue provenance is incomplete: "
                f"{sorted(missing_provenance)}"
            ),
            "revenue_at_risk_jod": None
        }

    calculation_timestamp = _parse_utc_timestamp(
        calculation_timestamp
    )
    calculation_window_start = _parse_utc_timestamp(
        calculation_window_start
    )
    calculation_window_end = _parse_utc_timestamp(
        calculation_window_end
    )

    if any(
        pd.isna(value)
        for value in [
            calculation_timestamp,
            calculation_window_start,
            calculation_window_end
        ]
    ):
        return {
            "status": "error",
            "reason": "Invalid revenue calculation timestamp or window"
        }

    if (
        calculation_window_end
        <= calculation_window_start
    ):
        return {
            "status": "error",
            "reason": (
                "calculation_window_end must be after "
                "calculation_window_start"
            )
        }

    # ---------------------------------------------------------
    # 6. حساب الطاقة المفقودة المتوقعة
    # ---------------------------------------------------------

    # لا نستخدم abs()
    # لاننا نحسب under-recorded / missing energy فقط
    expected_missing_kwh = max(
        0.0,
        float(
            expected_kwh
        )
        - float(
            reliable_observed_kwh
        )
    )

    # ---------------------------------------------------------
    # 7. حساب Gross Financial Exposure
    # ---------------------------------------------------------

    gross_exposure_jod = (
        expected_missing_kwh
        * float(
            tariff_jod_per_kwh
        )
    )

    # ---------------------------------------------------------
    # 8. حساب Low / Base / High scenarios
    # ---------------------------------------------------------

    revenue_low_jod = (
        gross_exposure_jod
        * float(
            recoverability_low
        )
    )

    revenue_base_jod = (
        gross_exposure_jod
        * float(
            recoverability_base
        )
    )

    revenue_high_jod = (
        gross_exposure_jod
        * float(
            recoverability_high
        )
    )

    if exclusions is None:
        exclusions = [
            "fixed charges",
            "taxes",
            "penalties"
        ]

    if (
        not isinstance(
            exclusions,
            list
        )
        or not all(
            isinstance(
                item,
                str
            )
            for item in exclusions
        )
    ):
        return {
            "status": "error",
            "reason": "exclusions must be a list of strings"
        }

    # ---------------------------------------------------------
    # 9. النتيجة النهائية
    # ---------------------------------------------------------

    return {
        "status": "success",

        "expected_kwh": round(
            float(
                expected_kwh
            ),
            4
        ),

        "reliable_observed_kwh": round(
            float(
                reliable_observed_kwh
            ),
            4
        ),

        "expected_missing_kwh": round(
            expected_missing_kwh,
            4
        ),

        "tariff": {
            "jod_per_kwh": float(
                tariff_jod_per_kwh
            ),
            "version": str(
                tariff_version
            ),
            "source": str(
                tariff_source
            ),
            "bracket_name": (
                str(
                    tariff_bracket_name
                )
                if tariff_bracket_name is not None
                else None
            ),
            "interpretation": (
                "Caller-resolved energy rate; "
                "fixed charges are excluded."
            )
        },

        "gross_exposure_jod": round(
            gross_exposure_jod,
            4
        ),

        "revenue_at_risk_jod": {
            "low": round(
                revenue_low_jod,
                4
            ),
            "base": round(
                revenue_base_jod,
                4
            ),
            "high": round(
                revenue_high_jod,
                4
            )
        },

        "recoverability": {
            "low": float(
                recoverability_low
            ),
            "base": float(
                recoverability_base
            ),
            "high": float(
                recoverability_high
            )
        },

        "confidence": {
            "data_reliability_confirmed": True,
            "quality_score": (
                float(
                    quality_score
                )
                if quality_score is not None
                else None
            ),
            "note": (
                "Financial estimates remain sensitive to the "
                "forecast, tariff, and recoverability assumptions."
            )
        },

        "calculation_timestamp": (
            calculation_timestamp.isoformat()
        ),

        "calculation_window": {
            "start": (
                calculation_window_start.isoformat()
            ),
            "end": (
                calculation_window_end.isoformat()
            )
        },

        "provenance": {
            "forecast_reference": str(
                forecast_reference
            ),
            "observed_reference": str(
                observed_reference
            ),
            "quality_reference": str(
                quality_reference
            )
        },

        "assumptions": [
            "Revenue at Risk is based on expected missing energy.",
            "Recoverability values are configurable sensitivity assumptions.",
            "The supplied tariff rate is assumed applicable to the calculation window.",
            "The estimate does not prove billing loss or fraud."
        ],

        "exclusions": exclusions,

        "method_version": "revenue_at_risk_v2"
    }

def calculate_triage_priority(
    case_id,
    technical_severity,
    scope_ratio,
    revenue_at_risk_jod,
    revenue_reference_jod,
    recurrence_score,
    upstream_evidence_score,
    data_confidence,
    waiting_sla_score,
    active_queue=None,
    revenue_scenario="base"
):
    """
    حساب Triage Priority من 0 الى 100.

    الهدف هو ترتيب الحالات حسب الاولوية التشغيلية
    باستخدام عدة عوامل مستقلة.

    active_queue تستخدم لحساب rank و percentile
    مقارنة بالحالات النشطة حاليا.

    evidence ownership:
    - scope_ratio يأتي من shared affected_fraction.
    - upstream_evidence_score يأتي من upstream evidence مستقل
      مثل energy-balance mismatch، وليس shared confidence.
    """

    # ---------------------------------------------------------
    # 1. التحقق من case_id
    # ---------------------------------------------------------

    if (
        case_id is None
        or not str(
            case_id
        ).strip()
    ):
        return {
            "status": "error",
            "reason": "case_id is required"
        }

    case_id = str(
        case_id
    )

    # ---------------------------------------------------------
    # 2. التحقق من Technical Severity
    # ---------------------------------------------------------

    technical_error = _validate_finite_number(
        technical_severity,
        "technical_severity",
        minimum=0.0,
        maximum=100.0
    )

    if technical_error is not None:
        return technical_error

    # ---------------------------------------------------------
    # 3. التحقق من العوامل الي لازم تكون بين 0 و 1
    # ---------------------------------------------------------

    normalized_inputs = {
        "scope_ratio": scope_ratio,
        "recurrence_score": recurrence_score,
        "upstream_evidence_score": upstream_evidence_score,
        "data_confidence": data_confidence,
        "waiting_sla_score": waiting_sla_score
    }

    for input_name, input_value in normalized_inputs.items():

        error = _validate_finite_number(
            input_value,
            input_name,
            minimum=0.0,
            maximum=1.0
        )

        if error is not None:
            return error

    # ---------------------------------------------------------
    # 4. التحقق من Revenue at Risk
    # ---------------------------------------------------------

    if revenue_scenario not in {
        "low",
        "base",
        "high"
    }:
        return {
            "status": "error",
            "reason": "revenue_scenario must be low, base, or high"
        }

    revenue_input_type = "scalar"

    if isinstance(
        revenue_at_risk_jod,
        dict
    ):
        revenue_values = revenue_at_risk_jod

        # لو caller مرر Revenue result كامل بدل nested dict.
        if isinstance(
            revenue_values.get(
                "revenue_at_risk_jod"
            ),
            dict
        ):
            revenue_values = revenue_values[
                "revenue_at_risk_jod"
            ]

        if revenue_scenario not in revenue_values:
            return {
                "status": "unknown",
                "reason": (
                    f"Revenue scenario '{revenue_scenario}' "
                    "is unavailable"
                )
            }

        selected_revenue = revenue_values[
            revenue_scenario
        ]

        revenue_input_type = (
            "low_base_high_scenarios"
        )

    else:
        selected_revenue = (
            revenue_at_risk_jod
        )

    revenue_error = _validate_finite_number(
        selected_revenue,
        "revenue_at_risk_jod",
        minimum=0.0
    )

    if revenue_error is not None:
        return revenue_error

    # ---------------------------------------------------------
    # 5. التحقق من Revenue Reference
    # ---------------------------------------------------------

    reference_error = _validate_finite_number(
        revenue_reference_jod,
        "revenue_reference_jod",
        minimum=0.000001
    )

    if reference_error is not None:
        return reference_error

    # ---------------------------------------------------------
    # 6. Normalize Technical Severity
    # ---------------------------------------------------------

    technical_normalized = (
        float(
            technical_severity
        ) / 100.0
    )

    # ---------------------------------------------------------
    # 7. Normalize Revenue at Risk
    # ---------------------------------------------------------

    # revenue_reference_jod هي قيمة configurable
    # تمثل المبلغ الذي نعتبر عنده Revenue contribution وصل للحد الاقصى
    revenue_normalized = min(
        float(
            selected_revenue
        )
        / float(
            revenue_reference_jod
        ),
        1.0
    )

    # ---------------------------------------------------------
    # 8. حساب مساهمة كل عامل
    # ---------------------------------------------------------

    technical_contribution = (
        technical_normalized * 25
    )

    scope_contribution = (
        float(
            scope_ratio
        ) * 20
    )

    revenue_contribution = (
        revenue_normalized * 20
    )

    recurrence_contribution = (
        float(
            recurrence_score
        ) * 10
    )

    upstream_contribution = (
        float(
            upstream_evidence_score
        ) * 10
    )

    confidence_contribution = (
        float(
            data_confidence
        ) * 10
    )

    waiting_contribution = (
        float(
            waiting_sla_score
        ) * 5
    )

    # ---------------------------------------------------------
    # 9. حساب Priority Score النهائي
    # ---------------------------------------------------------

    priority_score = (
        technical_contribution
        + scope_contribution
        + revenue_contribution
        + recurrence_contribution
        + upstream_contribution
        + confidence_contribution
        + waiting_contribution
    )

    priority_score = round(
        min(
            max(
                priority_score,
                0.0
            ),
            100.0
        ),
        2
    )

    # ---------------------------------------------------------
    # 10. تحديد Priority Band
    # ---------------------------------------------------------

    if priority_score >= 80:
        priority_band = "P1"

    elif priority_score >= 60:
        priority_band = "P2"

    elif priority_score >= 35:
        priority_band = "P3"

    else:
        priority_band = "P4"

    # ---------------------------------------------------------
    # 11. تجهيز Active Queue
    # ---------------------------------------------------------

    if active_queue is None:
        active_queue = []

    if not isinstance(
        active_queue,
        list
    ):
        return {
            "status": "error",
            "reason": "active_queue must be a list"
        }

    queue_scores = []
    seen_case_ids = set()

    for case in active_queue:

        if not isinstance(
            case,
            dict
        ):
            return {
                "status": "error",
                "reason": "Each active_queue item must be a dictionary"
            }

        queue_case_id = case.get(
            "case_id"
        )

        queue_priority_score = case.get(
            "priority_score"
        )

        if (
            queue_case_id is None
            or not str(
                queue_case_id
            ).strip()
        ):
            return {
                "status": "error",
                "reason": "Each queue case must contain case_id"
            }

        queue_case_id_normalized = str(
            queue_case_id
        )

        # duplicate IDs نعتبرها data error
        # بدل ما نضاعف وزن نفس الحالة في rank.
        if (
            queue_case_id_normalized
            in seen_case_ids
        ):
            return {
                "status": "error",
                "reason": (
                    "Duplicate case_id found in active_queue: "
                    f"{queue_case_id_normalized}"
                )
            }

        seen_case_ids.add(
            queue_case_id_normalized
        )

        queue_error = _validate_finite_number(
            queue_priority_score,
            "Queue priority_score",
            minimum=0.0,
            maximum=100.0
        )

        if queue_error is not None:
            return queue_error

        # اذا نفس الحالة موجودة في الـ queue
        # ما نضيف النسخة القديمة منها
        if (
            queue_case_id_normalized
            == case_id
        ):
            continue

        queue_scores.append({
            "case_id": (
                queue_case_id_normalized
            ),
            "priority_score": float(
                queue_priority_score
            )
        })

    # نضيف الحالة الحالية بالـ score الجديد
    queue_scores.append({
        "case_id": case_id,
        "priority_score": priority_score
    })

    # ---------------------------------------------------------
    # 12. حساب Rank
    # ---------------------------------------------------------

    # الحالات الي score تبعها اعلى منا
    # هي الي تكون قبلنا بالترتيب.
    # ties تحصل على نفس rank.
    higher_priority_cases = sum(
        1
        for case in queue_scores
        if (
            case[
                "priority_score"
            ] > priority_score
        )
    )

    rank = (
        higher_priority_cases
        + 1
    )

    # ---------------------------------------------------------
    # 13. حساب Percentile
    # ---------------------------------------------------------

    queue_size = len(
        queue_scores
    )

    if queue_size == 1:
        percentile = 100.0

    else:
        # percentile يحسب الحالات الأقل strict.
        # الحالات المتعادلة لا تعتبر اعلى ولا اقل.
        lower_priority_cases = sum(
            1
            for case in queue_scores
            if (
                case[
                    "priority_score"
                ] < priority_score
            )
        )

        percentile = (
            lower_priority_cases
            / (
                queue_size - 1
            )
        ) * 100

    percentile = round(
        percentile,
        2
    )

    # ---------------------------------------------------------
    # 14. النتيجة النهائية
    # ---------------------------------------------------------

    return {
        "status": "success",
        "case_id": case_id,

        "priority_score": priority_score,
        "priority_band": priority_band,

        "rank": rank,
        "queue_size": queue_size,
        "percentile": percentile,

        "normalized_components": {
            "technical_severity": round(
                technical_normalized,
                4
            ),
            "scope": round(
                float(
                    scope_ratio
                ),
                4
            ),
            "revenue_at_risk": round(
                revenue_normalized,
                4
            ),
            "recurrence": round(
                float(
                    recurrence_score
                ),
                4
            ),
            "upstream_evidence": round(
                float(
                    upstream_evidence_score
                ),
                4
            ),
            "data_confidence": round(
                float(
                    data_confidence
                ),
                4
            ),
            "waiting_sla": round(
                float(
                    waiting_sla_score
                ),
                4
            )
        },

        "contributions": {
            "technical_severity": round(
                technical_contribution,
                2
            ),
            "scope": round(
                scope_contribution,
                2
            ),
            "revenue_at_risk": round(
                revenue_contribution,
                2
            ),
            "recurrence": round(
                recurrence_contribution,
                2
            ),
            "upstream_evidence": round(
                upstream_contribution,
                2
            ),
            "data_confidence": round(
                confidence_contribution,
                2
            ),
            "waiting_sla": round(
                waiting_contribution,
                2
            )
        },

        "weights": {
            "technical_severity": 25,
            "scope": 20,
            "revenue_at_risk": 20,
            "recurrence": 10,
            "upstream_evidence": 10,
            "data_confidence": 10,
            "waiting_sla": 5
        },

        "revenue": {
            "scenario_used": revenue_scenario,
            "input_type": revenue_input_type,
            "selected_jod": float(
                selected_revenue
            ),
            "reference_jod": float(
                revenue_reference_jod
            )
        },

        "queue_semantics": {
            "duplicate_case_ids": "rejected",
            "current_case": "old_queue_entry_replaced",
            "rank_ties": "same_rank_strictly_greater_count",
            "percentile_ties": "ties_excluded_from_lower_count"
        },

        "evidence_ownership": {
            "scope": (
                "shared affected_fraction"
            ),
            "upstream_evidence": (
                "independent upstream evidence such as energy balance"
            ),
            "data_confidence": (
                "reading quality score"
            )
        },

        "limitations": [
            (
                "Scope and upstream evidence must represent "
                "different signals to avoid double counting."
            ),
            (
                "Revenue normalization depends on the configured "
                "revenue_reference_jod."
            )
        ],

        "method_version": "triage_priority_v2"
    }
