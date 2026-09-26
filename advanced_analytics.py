# Day 5

import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest

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

    # 2. التأكد من وجود الأعمدة المطلوبة

    required_columns = {
        "Meter_ID",
        "DateTime",
        "KWH/hh (per half hour)"
    }

    missing_columns = required_columns - set(readings_df.columns)

    if missing_columns:
        return {
            "status": "error",
            "reason": f"Missing required columns: {sorted(missing_columns)}"
        }

    # 3. تحويل وقت الهدف إلى datetime
    

    target_timestamp = pd.to_datetime(
        target_timestamp,
        errors="coerce"
    )

    if pd.isna(target_timestamp):
        return {
            "status": "error",
            "reason": "Invalid target_timestamp"
        }

    
    # 4. تجهيز نسخة من البيانات
    

    # بنعمل نسخة عشان ما نعدل على الداتا الاصلية
    df = readings_df.copy()

    # تحويل عمود الوقت إلى datetime
    df["DateTime"] = pd.to_datetime(
        df["DateTime"],
        errors="coerce"
    )

    # تحويل قراءات الاستهلاك إلى ارقام
    df["KWH/hh (per half hour)"] = pd.to_numeric(
        df["KWH/hh (per half hour)"],
        errors="coerce"
    )

    # حذف القراءات الي ما فيها وقت او استهلاك صالح
    df = df.dropna(
        subset=[
            "DateTime",
            "KWH/hh (per half hour)"
        ]
    )

    # 5. جلب القراءات التاريخية للعداد
     

    # نجيب قراءات العداد المطلوب فقط
    # ونستخدم القراءات السابقة للوقت الي بدنا نتوقعه فقط
    # عشان ما يصير عندنا تسرب بيانات من المستقبل
    historical_df = df[
        (df["Meter_ID"] == meter_id) &
        (df["DateTime"] < target_timestamp)
    ].copy()

    if historical_df.empty:
        return {
            "status": "unknown",
            "reason": "No historical readings available before target timestamp"
        }

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
        (historical_df["DateTime"].dt.dayofweek == target_weekday) &
        (historical_df["DateTime"].dt.hour == target_hour) &
        (historical_df["DateTime"].dt.minute == target_minute)
    ].copy()

    
    # 8. حساب الاستهلاك المتوقع
    #

    if len(seasonal_df) >= min_samples:

        # اذا عندنا عدد كافي من القراءات لنفس اليوم ونفس الوقت
        # بنستخدم median عشان نقلل تأثير القراءات الشاذة
        expected_kwh = seasonal_df[
            "KWH/hh (per half hour)"
        ].median()

        method = "seasonal_weekday_halfhour"

        support_count = len(seasonal_df)

    else:

        # اذا ما كان عندنا بيانات كافية لنفس اليوم
        # بنستخدم نفس الوقت من الايام السابقة كـ fallback
        fallback_df = historical_df[
            (historical_df["DateTime"].dt.hour == target_hour) &
            (historical_df["DateTime"].dt.minute == target_minute)
        ].copy()

        if len(fallback_df) >= min_samples:

            # نحسب median لنفس الوقت من الايام السابقة
            expected_kwh = fallback_df[
                "KWH/hh (per half hour)"
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
                "target_timestamp": str(target_timestamp),
                "reason": "Insufficient historical readings for forecast",
                "seasonal_samples": len(seasonal_df),
                "fallback_samples": len(fallback_df)
            }

    # 9. النتيجة النهائية للتوقع

    return {
        "status": "success",
        "meter_id": meter_id,
        "target_timestamp": str(target_timestamp),

        # الاستهلاك المتوقع للعداد
        "expected_kwh": float(expected_kwh),

        # الطريقة الي استخدمناها عشان نحسب التوقع
        "method": method,

        # عدد القراءات الي اعتمدنا عليها
        "support_count": support_count,

        # اقل عدد قراءات مطلوب حتى نعتبر التوقع صالح
        "min_samples": min_samples,

        # نسخة طريقة التوقع عشان نقدر نتتبع الحسابات لاحقا
        "method_version": "v1"
    }
"""
    حساب توازن الطاقة بين قراءة المحول
    ومجموع قراءات العدادات المتصلة فيه.

    يتم احتساب نسبة التيكنكال لوس المتوقعة
    ثم مقارنة القراءة المتوقعة للمحول مع القراءة الفعلية.
    """

def calculate_energy_balance(
    transformer_reading,
    meter_readings,
    technical_loss_rate=0.03,
    tolerance_pct=0.05
):  # الهامش الي بسمح فيه قبل ما اقول انه في عندي imbalance

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

    if technical_loss_rate < 0:
        return {
            "status": "error",
            "reason": "technical_loss_rate cannot be negative"
        }

    if tolerance_pct < 0:
        return {
            "status": "error",
            "reason": "tolerance_pct cannot be negative"
        }

    #         -
    # 2. تحويل القراءات الى ارقام والتأكد انها صالحة
    #          -

    try:
        transformer_reading = float(transformer_reading)

        meter_readings = [
            float(reading)
            for reading in meter_readings
        ]

    except (TypeError, ValueError):
        return {
            "status": "error",
            "reason": "Transformer and meter readings must be numeric"
        }

    # التأكد انه ما عندنا قيم NaN
    if pd.isna(transformer_reading) or any(
        pd.isna(reading) for reading in meter_readings
    ):
        return {
            "status": "error",
            "reason": "Transformer and meter readings cannot contain NaN"
        }

    # 3. حساب مجموع قراءات العدادات

    downstream_kwh = sum(meter_readings)

    
    # 4. حساب القراءة المتوقعة للمحول

    # نضيف نسبة الفقد التقني على مجموع قراءات العدادات
    expected_transformer_kwh = downstream_kwh * (
        1 + technical_loss_rate
    )
            
    
    # 5. حساب الفرق بين القراءة الفعلية والمتوقعة
    

    difference_kwh = abs(
        transformer_reading - expected_transformer_kwh
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
                "difference_pct": None,
                "tolerance_pct": tolerance_pct,
                "technical_loss_rate": technical_loss_rate,
                "reason": (
                    "Expected transformer usage is zero "
                    "while actual usage is non-zero"
                ),
                "method_version": "v1"
            }

    else:

        # حساب نسبة الفرق بين القراءة الفعلية والمتوقعة
        difference_pct = (
            difference_kwh / expected_transformer_kwh
        )

    #                   -
    # 7. تحديد اذا كان المحول متوازن او لا
    #                    -

    if difference_pct <= tolerance_pct:
        balance_status = "balanced"
    else:
        balance_status = "imbalanced"

    #                    -
    # 8. النتيجة النهائية
    #                                         

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
        "difference_kwh": float(difference_kwh),

        # نسبة الفرق بين القراءة الفعلية والمتوقعة
        "difference_pct": float(difference_pct),

        # الهامش الي بسمح فيه قبل ما اقول انه في عندي imbalance
        "tolerance_pct": tolerance_pct,

        # نسبة الفقد التقني المستخدمة بالحساب
        "technical_loss_rate": technical_loss_rate,

        # النتيجة النهائية للتوازن
        "balance_status": balance_status,

        # نسخة طريقة الحساب عشان نقدر نتتبع النتائج لاحقا
        "method_version": "v1"
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

    if not 0 < contamination <= 0.5:
        return {
            "status": "error",
            "reason": "contamination must be between 0 and 0.5"
        }

    # 2. التأكد من وجود الاعمدة المطلوبة
    required_columns = {
        "Meter_ID",
        "DateTime",
        "KWH/hh (per half hour)"
    }

    missing_columns = required_columns - set(readings_df.columns)

    if missing_columns:
        return {
            "status": "error",
            "reason": f"Missing required columns: {sorted(missing_columns)}"
        }
    # 3. تجهيز الوقت والبيانات
    target_timestamp = pd.to_datetime(
        target_timestamp,
        errors="coerce"
    )

    if pd.isna(target_timestamp):
        return {
            "status": "error",
            "reason": "Invalid target_timestamp"
        }

    df = readings_df.copy()

    df["DateTime"] = pd.to_datetime(
        df["DateTime"],
        errors="coerce"
    )

    df["KWH/hh (per half hour)"] = pd.to_numeric(
        df["KWH/hh (per half hour)"],
        errors="coerce"
    )

    df = df.dropna(
        subset=["DateTime", "KWH/hh (per half hour)"]
    )
    # 4. جلب تاريخ العداد السابق فقط
    historical_df = df[
        (df["Meter_ID"] == meter_id) &
        (df["DateTime"] < target_timestamp)
    ].copy()

    # ترتيب القراءات حسب الوقت
    historical_df = historical_df.sort_values("DateTime")

    if historical_df.empty:
        return {
            "status": "unknown",
            "reason": "No historical readings available before target timestamp"
        }

    # 5. Feature Engineering
    # حساب نسبة التغير عن القراءة السابقة
    # استخدمنا symmetric change عشان نتجنب مشكلة القسمة على صفر
    previous_values = historical_df[
        "KWH/hh (per half hour)"
    ].shift(1)

    current_values = historical_df[
        "KWH/hh (per half hour)"
    ]

    change_denominator = (
        current_values.abs() + previous_values.abs()
    )

    historical_df["change_pct"] = np.where(
        change_denominator == 0,
        0.0,
        2 * (current_values - previous_values) / change_denominator
    )
    # حساب متوسط اخر 6 قراءات سابقة
    # shift(1) عشان القراءة الحالية ما تدخل بحساب المتوسط الخاص فيها
    historical_df["rolling_mean"] = historical_df[
        "KWH/hh (per half hour)"
    ].shift(1).rolling(window=6).mean()

    # حساب مقدار التذبذب في اخر 6 قراءات سابقة
    historical_df["rolling_std"] = historical_df[
        "KWH/hh (per half hour)"
    ].shift(1).rolling(window=6).std()

    # 6. تجهيز الـ features للموديل
    feature_columns = [
        "KWH/hh (per half hour)",
        "change_pct",
        "rolling_mean",
        "rolling_std"
    ]
    # حذف الصفوف الي ما اكتملت فيها الـ features
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
    X_train = model_df[feature_columns]
    # 7. تدريب Isolation Forest
    model = IsolationForest(
        contamination=contamination,
        random_state=random_state
    )

    model.fit(X_train)
    # 8. جلب القراءة المستهدفة
    target_df = df[
        (df["Meter_ID"] == meter_id) &
        (df["DateTime"] == target_timestamp)
    ].copy()

    if target_df.empty:
        return {
            "status": "unknown",
            "reason": "No reading found at target timestamp"
        }

    # اذا كان عندنا اكثر من قراءة لنفس العداد ونفس الوقت
    # نعتبر البيانات غير صالحة للتحليل
    if len(target_df) > 1:
        return {
            "status": "error",
            "reason": "Multiple readings found for meter at target timestamp"
        }

    target_reading = float(
        target_df.iloc[0]["KWH/hh (per half hour)"]
    )
    # 9. تجهيز features للقراءة المستهدفة
    previous_reading = float(
        historical_df[
            "KWH/hh (per half hour)"
        ].iloc[-1]
    )
    # حساب التغير بين القراءة الحالية والقراءة السابقة
    # بنفس الطريقة الي استخدمناها في training data
    target_change_denominator = (
        abs(target_reading) + abs(previous_reading)
    )
    if target_change_denominator == 0:
        target_change_pct = 0.0
    else:
        target_change_pct = (
            2 * (target_reading - previous_reading)
            / target_change_denominator
        )
    # لازم يكون عندنا 6 قراءات سابقة على الاقل
    # عشان نحسب rolling mean و rolling std
    if len(historical_df) < 6:
        return {
            "status": "unknown",
            "reason": "Insufficient previous readings for target rolling features",
            "available_samples": len(historical_df),
            "required_samples": 6
        }

    # اخر 6 قراءات قبل القراءة المستهدفة
    previous_window = historical_df[
        "KWH/hh (per half hour)"
    ].tail(6)

    # متوسط اخر 6 قراءات قبل القراءة المستهدفة
    target_rolling_mean = previous_window.mean()

    # مقدار التذبذب في اخر 6 قراءات قبل القراءة المستهدفة
    target_rolling_std = previous_window.std()

    if pd.isna(target_rolling_mean) or pd.isna(target_rolling_std):
        return {
            "status": "unknown",
            "reason": "Unable to calculate target rolling features"
        }
    # 10. تجهيز القراءة المستهدفة للموديل
    X_target = pd.DataFrame(
        [{
            "KWH/hh (per half hour)": target_reading,
            "change_pct": target_change_pct,
            "rolling_mean": target_rolling_mean,
            "rolling_std": target_rolling_std
        }],
        columns=feature_columns
    )
    # 11. تشغيل الموديل على القراءة المستهدفة
    prediction = int(
        model.predict(X_target)[0]
    )

    isolation_score = float(
        model.decision_function(X_target)[0]
    )
    # Isolation Forest:
    # 1 يعني normal
    # -1 يعني anomaly
    is_anomaly = prediction == -1
    if is_anomaly:
        isolation_label = "anomaly"
    else:
        isolation_label = "normal"
    # 12. النتيجة النهائية
    return {
        "status": "success",
        "meter_id": meter_id,
        "target_timestamp": str(target_timestamp),
        # القراءة الي فحصناها
        "target_reading_kwh": float(target_reading),
        # نتيجة Isolation Forest
        "is_anomaly": is_anomaly,
        "isolation_label": isolation_label,
        # هذا score للموديل وليس probability
        "isolation_score": isolation_score,
        # الـ features الخاصة بالقراءة الي فحصناها
        "target_features": {
            "reading_kwh": float(target_reading),
            "change_pct": float(target_change_pct),
            "rolling_mean": float(target_rolling_mean),
            "rolling_std": float(target_rolling_std)
        },
        # عدد القراءات الي تدرب عليها الموديل
        "training_samples": len(X_train),
        # اعدادات الموديل عشان نقدر نعيد نفس النتيجة
        "contamination": contamination,
        "random_state": random_state,
        # الـ features الي استخدمناها
        "features_used": [
            "reading_kwh",
            "symmetric_change",
            "rolling_mean",
            "rolling_std"
        ],
        # نسخة الطريقة عشان نقدر نتتبع النتائج لاحقا
        "method_version": "isolation_forest_v1"
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
    usage_deviation_threshold=0.20
):
    """
    تحليل ما اذا كان سياق الطقس متوافق مع التغير الموجود
    في استهلاك الطاقة وقت الحدث.

    النتيجة تعتبر weather evidence فقط
    وليست اثبات ان الطقس هو سبب الـ anomaly.
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
    # 2. التأكد من وجود الاعمدة المطلوبة
    required_columns = {
        timestamp_col,
        temperature_col
    }
    missing_columns = required_columns - set(weather_df.columns)

    if missing_columns:
        return {
            "status": "error",
            "reason": f"Missing weather columns: {sorted(missing_columns)}"
        }
    # 3. تجهيز الوقت والبيانات
    target_timestamp = pd.to_datetime(
        target_timestamp,
        errors="coerce"
    )

    if pd.isna(target_timestamp):
        return {
            "status": "error",
            "reason": "Invalid target_timestamp"
        }

    weather = weather_df.copy()

    weather[timestamp_col] = pd.to_datetime(
        weather[timestamp_col],
        errors="coerce"
    )

    weather[temperature_col] = pd.to_numeric(
        weather[temperature_col],
        errors="coerce"
    )

    weather = weather.dropna(
        subset=[
            timestamp_col,
            temperature_col
        ]
    )

    if weather.empty:
        return {
            "status": "unknown",
            "reason": "No valid weather observations are available"
        }
    # 4. جلب اقرب قراءة طقس لوقت الحدث
    weather["time_difference"] = (
        weather[timestamp_col] - target_timestamp
    ).abs()

    nearest_index = weather[
        "time_difference"
    ].idxmin()

    nearest_weather = weather.loc[
        nearest_index
    ]

    time_difference_minutes = (
        nearest_weather["time_difference"].total_seconds()
        / 60.0
    )
    if time_difference_minutes > alignment_tolerance_minutes:
        return {
            "status": "unknown",
            "reason": "No weather observation close enough to target timestamp",
            "nearest_weather_minutes": float(time_difference_minutes),
            "alignment_tolerance_minutes": alignment_tolerance_minutes
        }
    # 5. حساب تغير الاستهلاك عن المتوقع
    actual_kwh = float(actual_kwh)
    expected_kwh = float(expected_kwh)

    # اذا الاستهلاك المتوقع صفر
    # ما بنقدر نحسب نسبة التغير بطريقة عادية
    if expected_kwh <= 0:
        return {
            "status": "unknown",
            "reason": "Expected usage must be greater than zero for weather alignment"
        }

    usage_deviation_pct = (
        actual_kwh - expected_kwh
    ) / expected_kwh

    temperature_c = float(
        nearest_weather[temperature_col]
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
        usage_deviation_pct >= usage_deviation_threshold
    )

    extreme_temperature = (
        temperature_c >= hot_threshold_c
        or temperature_c <= cold_threshold_c
    )

    # ارتفاع الاستهلاك مع حرارة او برودة شديدة
    # يعتبر supporting evidence فقط
    if usage_increase and extreme_temperature:
        weather_evidence = "supporting"

    else:
        weather_evidence = "not_supporting"
    # 8. تحديد confidence بسيط للـ weather evidence
    if time_difference_minutes <= 30:
        alignment_confidence = 1.0
    else:
        alignment_confidence = 0.8
    # 9. النتيجة النهائية
    return {
        "status": "success",
        "target_timestamp": str(target_timestamp),

        # وقت اقرب قراءة طقس تم استخدامها
        "weather_timestamp": str(
            nearest_weather[timestamp_col]
        ),

        # درجة الحرارة وقت الحدث
        "temperature_c": temperature_c,

        # حالة الطقس بشكل مبسط
        "weather_condition": weather_condition,

        # الاستهلاك الفعلي والمتوقع
        "actual_kwh": actual_kwh,
        "expected_kwh": expected_kwh,

        # مقدار تغير الاستهلاك عن المتوقع
        "usage_deviation_pct": float(
            usage_deviation_pct
        ),

        # هل الطقس يدعم التفسير او لا
        "weather_evidence": weather_evidence,

        # الفرق الزمني بين الحدث وقراءة الطقس
        "alignment_minutes": float(
            time_difference_minutes
        ),

        # confidence خاص بجودة محاذاة الوقت
        "alignment_confidence": alignment_confidence,

        # الحدود المستخدمة في التحليل
        "thresholds": {
            "hot_threshold_c": hot_threshold_c,
            "cold_threshold_c": cold_threshold_c,
            "usage_deviation_threshold": usage_deviation_threshold,
            "alignment_tolerance_minutes": alignment_tolerance_minutes
        },

        # تذكير مهم ان النتيجة evidence وليست proof
        "limitation": (
            "Weather alignment is supporting context only "
            "and does not prove that weather caused the usage change."
        ),

        # نسخة الطريقة حتى نقدر نتتبع النتائج
        "method_version": "weather_alignment_v1"
    }
def analyze_customer_der_context(
    readings_df,
    customer_metadata_df,
    meter_id,
    target_timestamp,
    actual_kwh,
    expected_kwh,
    lookback_intervals=4
):
    """
    تحليل سياق العميل و DER مثل EV و Solar
    لمعرفة اذا كانت البيانات تدعم احد التفسيرات المحتملة.

    النتيجة تعتبر evidence فقط
    وليست اثبات للسبب الحقيقي للـ anomaly.
    """
    # 1. التحقق من البيانات
    if readings_df is None or readings_df.empty:
        return {
            "status": "error",
            "reason": "readings_df is required"
        }

    if customer_metadata_df is None or customer_metadata_df.empty:
        return {
            "status": "unknown",
            "reason": "Customer metadata is unavailable"
        }

    if not meter_id:
        return {
            "status": "error",
            "reason": "meter_id is required"
        }

    if target_timestamp is None:
        return {
            "status": "error",
            "reason": "target_timestamp is required"
        }

    if actual_kwh is None or expected_kwh is None:
        return {
            "status": "unknown",
            "reason": "Actual and expected usage are required"
        }

    # 2. تجهيز الوقت
    target_timestamp = pd.to_datetime(
        target_timestamp,
        errors="coerce"
    )

    if pd.isna(target_timestamp):
        return {
            "status": "error",
            "reason": "Invalid target_timestamp"
        }

    # 3. جلب metadata الخاصة بالعداد

    meter_metadata = customer_metadata_df[
        customer_metadata_df["Meter_ID"] == meter_id
    ]

    if meter_metadata.empty:
        return {
            "status": "unknown",
            "reason": "No customer metadata found for meter"
        }

    metadata = meter_metadata.iloc[0]

    has_ev = metadata.get("Has_EV", None)
    has_solar = metadata.get("Has_Solar", None)
    # 4. تجهيز قراءات العداد
    df = readings_df.copy()

    df["DateTime"] = pd.to_datetime(
        df["DateTime"],
        errors="coerce"
    )

    df["KWH/hh (per half hour)"] = pd.to_numeric(
        df["KWH/hh (per half hour)"],
        errors="coerce"
    )

    df = df.dropna(
        subset=[
            "DateTime",
            "KWH/hh (per half hour)"
        ]
    )

    meter_df = df[
        df["Meter_ID"] == meter_id
    ].sort_values("DateTime")

    # 5. حساب مقدار التغير عن المتوقع
    actual_kwh = float(actual_kwh)
    expected_kwh = float(expected_kwh)

    if expected_kwh <= 0:
        usage_deviation_pct = None
    else:
        usage_deviation_pct = (
            actual_kwh - expected_kwh
        ) / expected_kwh
    # 6. جلب القراءات السابقة للحدث
    previous_readings = meter_df[
        meter_df["DateTime"] < target_timestamp
    ].tail(lookback_intervals)

    if previous_readings.empty:
        persistence = False
    else:
        previous_mean = previous_readings[
            "KWH/hh (per half hour)"
        ].mean()

        persistence = (
            usage_deviation_pct is not None
            and abs(actual_kwh - previous_mean)
            > 0.20 * max(previous_mean, 0.001)
        )

    target_hour = target_timestamp.hour
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

        evening_event = 17 <= target_hour <= 23

        usage_increase = (
            usage_deviation_pct is not None
            and usage_deviation_pct >= 0.20
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
                "Load change persists across recent intervals"
            )

        if (
            evening_event
            and usage_increase
            and persistence
        ):
            ev_evidence = "supporting"
        else:
            ev_evidence = "not_supporting"

        ev_limitations.append(
            "The pattern is not unique to EV charging"
        )

        ev_limitations.append(
            "HVAC, water heating, appliances, or customer behavior may produce similar load"
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

        midday_event = 10 <= target_hour <= 15

        usage_drop = (
            usage_deviation_pct is not None
            and usage_deviation_pct <= -0.20
        )

        if midday_event:
            solar_reasons.append(
                "Usage drop occurs during daylight hours"
            )

        if usage_drop:
            solar_reasons.append(
                "Net load is meaningfully below expected consumption"
            )

        if midday_event and usage_drop:
            solar_evidence = "supporting"
        else:
            solar_evidence = "not_supporting"

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
        usage_deviation_pct is not None
        and abs(usage_deviation_pct) >= 0.20
        and persistence
    ):
        behavior_evidence = "supporting"

        behavior_reasons.append(
            "Usage differs meaningfully from expected consumption"
        )

        behavior_reasons.append(
            "The change persists across recent intervals"
        )

    else:
        behavior_evidence = "not_supporting"

    behavior_limitations.append(
        "Consumption patterns alone cannot identify the exact customer activity"
    )
    # 10. النتيجة النهائية
    return {
        "status": "success",
        "meter_id": meter_id,
        "target_timestamp": str(target_timestamp),

        "actual_kwh": actual_kwh,
        "expected_kwh": expected_kwh,
        "usage_deviation_pct": usage_deviation_pct,

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

        "method_version": "customer_der_context_v1"
    }
def calculate_anomaly_severity(
    usage_deviation_pct,
    rule_based_score,
    isolation_score,
    peer_network_score
):
    """
    حساب شدة الـ anomaly من 0 الى 100
    باستخدام عدة مصادر evidence مستقلة.

    الـ inputs تدخل بقيمها الاصلية،
    وبعدها يتم normalize كل component الى 0-1.
    """

    # ---------------------------------------------------------
    # 1. التحقق من الـ inputs
    # ---------------------------------------------------------

    raw_inputs = {
        "usage_deviation_pct": usage_deviation_pct,
        "rule_based_score": rule_based_score,
        "isolation_score": isolation_score,
        "peer_network_score": peer_network_score
    }

    for input_name, input_value in raw_inputs.items():

        # اذا القيمة غير موجودة
        if input_value is None:
            return {
                "status": "unknown",
                "reason": f"{input_name} is required for severity calculation"
            }

        # نتأكد ان القيمة رقم
        # bool بنرفضه رغم انه Python تعتبره نوع من int
        if isinstance(input_value, bool) or not isinstance(
            input_value,
            (int, float, np.number)
        ):
            return {
                "status": "error",
                "reason": f"{input_name} must be numeric"
            }

        # نتأكد انه مش NaN او infinity
        if not np.isfinite(input_value):
            return {
                "status": "error",
                "reason": f"{input_name} must be a finite numeric value"
            }

    # ---------------------------------------------------------
    # 2. Normalize Usage Deviation
    # ---------------------------------------------------------

    # نستخدم absolute value لان الـ spike والـ drop
    # الاثنين ممكن يمثلوا anomaly قوية
    usage_normalized = min(
        abs(float(usage_deviation_pct)),
        1.0
    )

    # ---------------------------------------------------------
    # 3. Normalize Rule-Based Evidence
    # ---------------------------------------------------------

    # الـ rule-based score في Day 5 متوقع يكون من 0 الى 100
    rule_normalized = min(
        max(float(rule_based_score) / 100.0, 0.0),
        1.0
    )

    # ---------------------------------------------------------
    # 4. Normalize Isolation Forest Evidence
    # ---------------------------------------------------------

    # Isolation Forest score ليس probability
    # القيم السالبة تعتبر evidence اقوى للـ anomaly

    if isolation_score >= 0:
        isolation_normalized = 0.0
    else:
        isolation_normalized = min(
            abs(float(isolation_score)) / 0.2,
            1.0
        )

    # ---------------------------------------------------------
    # 5. Normalize Peer / Network Evidence
    # ---------------------------------------------------------

    # peer_network_score متوقع يكون ratio من 0 الى 1
    peer_normalized = min(
        max(float(peer_network_score), 0.0),
        1.0
    )

    # ---------------------------------------------------------
    # 6. حساب مساهمة كل evidence source
    # ---------------------------------------------------------

    usage_contribution = usage_normalized * 35
    rule_contribution = rule_normalized * 25
    isolation_contribution = isolation_normalized * 20
    peer_contribution = peer_normalized * 20

    # ---------------------------------------------------------
    # 7. حساب Severity النهائي
    # ---------------------------------------------------------

    severity_score = (
        usage_contribution
        + rule_contribution
        + isolation_contribution
        + peer_contribution
    )

    severity_score = round(
        min(max(severity_score, 0.0), 100.0),
        2
    )

    # ---------------------------------------------------------
    # 8. النتيجة النهائية
    # ---------------------------------------------------------

    return {
        "status": "success",
        "severity_score": severity_score,

        "normalized_components": {
            "usage_deviation": round(usage_normalized, 4),
            "rule_based": round(rule_normalized, 4),
            "isolation_forest": round(isolation_normalized, 4),
            "peer_network": round(peer_normalized, 4)
        },

        "contributions": {
            "usage_deviation": round(usage_contribution, 2),
            "rule_based": round(rule_contribution, 2),
            "isolation_forest": round(isolation_contribution, 2),
            "peer_network": round(peer_contribution, 2)
        },

        "weights": {
            "usage_deviation": 35,
            "rule_based": 25,
            "isolation_forest": 20,
            "peer_network": 20
        },

        "calibration": {
            "usage_cap": 1.0,
            "rule_based_max": 100.0,
            "isolation_negative_reference": -0.2,
            "peer_network_range": "0-1"
        },

        "method_version": "hybrid_severity_v1"
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
    calculation_timestamp=None
):
    """
    تقدير Revenue at Risk بالدينار الاردني.

    يتم حساب الطاقة المفقودة المتوقعة،
    ثم تطبيق التعرفة ونسب recoverability
    لاخراج low / base / high estimates.

    قيم recoverability هي sensitivity assumptions
    وليست thresholds لشدة الخسارة.
    """

    # ---------------------------------------------------------
    # 1. التحقق من بيانات الطاقة
    # ---------------------------------------------------------

    numeric_inputs = {
        "expected_kwh": expected_kwh,
        "reliable_observed_kwh": reliable_observed_kwh
    }

    for input_name, input_value in numeric_inputs.items():

        if input_value is None:
            return {
                "status": "unknown",
                "reason": f"{input_name} is required for revenue estimation"
            }

        if isinstance(input_value, bool) or not isinstance(
            input_value,
            (int, float, np.number)
        ):
            return {
                "status": "error",
                "reason": f"{input_name} must be numeric"
            }

        if not np.isfinite(input_value):
            return {
                "status": "error",
                "reason": f"{input_name} must be a finite numeric value"
            }

        if input_value < 0:
            return {
                "status": "error",
                "reason": f"{input_name} cannot be negative"
            }

    # ---------------------------------------------------------
    # 2. التحقق من موثوقية البيانات
    # ---------------------------------------------------------

    if data_reliable is not True:
        return {
            "status": "unknown",
            "reason": "Observed usage is not reliable enough for revenue estimation"
        }

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

    if (
        isinstance(tariff_jod_per_kwh, bool)
        or not isinstance(
            tariff_jod_per_kwh,
            (int, float, np.number)
        )
    ):
        return {
            "status": "error",
            "reason": "tariff_jod_per_kwh must be numeric"
        }

    if (
        not np.isfinite(tariff_jod_per_kwh)
        or tariff_jod_per_kwh < 0
    ):
        return {
            "status": "error",
            "reason": "tariff_jod_per_kwh must be a valid non-negative value"
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

        if isinstance(input_value, bool) or not isinstance(
            input_value,
            (int, float, np.number)
        ):
            return {
                "status": "error",
                "reason": f"{input_name} must be numeric"
            }

        if not np.isfinite(input_value):
            return {
                "status": "error",
                "reason": f"{input_name} must be finite"
            }

        if not 0 <= input_value <= 1:
            return {
                "status": "error",
                "reason": f"{input_name} must be between 0 and 1"
            }

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
    # 5. تجهيز وقت الحساب
    # ---------------------------------------------------------

    if calculation_timestamp is not None:

        calculation_timestamp = pd.to_datetime(
            calculation_timestamp,
            errors="coerce"
        )

        if pd.isna(calculation_timestamp):
            return {
                "status": "error",
                "reason": "Invalid calculation_timestamp"
            }

    # ---------------------------------------------------------
    # 6. حساب الطاقة المفقودة المتوقعة
    # ---------------------------------------------------------

    # لا نستخدم abs()
    # لاننا نحسب under-recorded / missing energy فقط
    expected_missing_kwh = max(
        0.0,
        float(expected_kwh) - float(reliable_observed_kwh)
    )

    # ---------------------------------------------------------
    # 7. حساب Gross Financial Exposure
    # ---------------------------------------------------------

    gross_exposure_jod = (
        expected_missing_kwh
        * float(tariff_jod_per_kwh)
    )

    # ---------------------------------------------------------
    # 8. حساب Low / Base / High scenarios
    # ---------------------------------------------------------

    revenue_low_jod = (
        gross_exposure_jod
        * float(recoverability_low)
    )

    revenue_base_jod = (
        gross_exposure_jod
        * float(recoverability_base)
    )

    revenue_high_jod = (
        gross_exposure_jod
        * float(recoverability_high)
    )

    # ---------------------------------------------------------
    # 9. النتيجة النهائية
    # ---------------------------------------------------------

    return {
        "status": "success",

        "expected_kwh": round(
            float(expected_kwh),
            4
        ),

        "reliable_observed_kwh": round(
            float(reliable_observed_kwh),
            4
        ),

        "expected_missing_kwh": round(
            expected_missing_kwh,
            4
        ),

        "tariff": {
            "jod_per_kwh": float(tariff_jod_per_kwh),
            "version": tariff_version
        },

        "gross_exposure_jod": round(
            gross_exposure_jod,
            4
        ),

        "revenue_at_risk_jod": {
            "low": round(revenue_low_jod, 4),
            "base": round(revenue_base_jod, 4),
            "high": round(revenue_high_jod, 4)
        },

        "recoverability": {
            "low": float(recoverability_low),
            "base": float(recoverability_base),
            "high": float(recoverability_high)
        },

        "confidence": {
            "data_reliability_confirmed": True,
            "note": (
                "Financial estimates remain sensitive to the "
                "forecast, tariff, and recoverability assumptions."
            )
        },

        "calculation_timestamp": (
            str(calculation_timestamp)
            if calculation_timestamp is not None
            else None
        ),

        "assumptions": [
            "Revenue at Risk is based on expected missing energy.",
            "Recoverability values are configurable sensitivity assumptions.",
            "The estimate does not prove billing loss or fraud."
        ],

        "method_version": "revenue_at_risk_v1"
    }
def calculate_triage_priority(
    case_id,
    technical_severity,
    scope_ratio,
    revenue_at_risk_jod,
    revenue_reference_jod,
    recurrence_score,
    upstream_shared_score,
    data_confidence,
    waiting_sla_score,
    active_queue=None
):
    """
    حساب Triage Priority من 0 الى 100.

    الهدف هو ترتيب الحالات حسب الاولوية التشغيلية
    باستخدام عدة عوامل مستقلة.

    active_queue تستخدم لحساب rank و percentile
    مقارنة بالحالات النشطة حاليا.
    """

    # ---------------------------------------------------------
    # 1. التحقق من case_id
    # ---------------------------------------------------------

    if case_id is None or not str(case_id).strip():
        return {
            "status": "error",
            "reason": "case_id is required"
        }

    # ---------------------------------------------------------
    # 2. التحقق من Technical Severity
    # ---------------------------------------------------------

    if (
        isinstance(technical_severity, bool)
        or not isinstance(
            technical_severity,
            (int, float, np.number)
        )
    ):
        return {
            "status": "error",
            "reason": "technical_severity must be numeric"
        }

    if not np.isfinite(technical_severity):
        return {
            "status": "error",
            "reason": "technical_severity must be finite"
        }

    if not 0 <= technical_severity <= 100:
        return {
            "status": "error",
            "reason": "technical_severity must be between 0 and 100"
        }

    # ---------------------------------------------------------
    # 3. التحقق من العوامل الي لازم تكون بين 0 و 1
    # ---------------------------------------------------------

    normalized_inputs = {
        "scope_ratio": scope_ratio,
        "recurrence_score": recurrence_score,
        "upstream_shared_score": upstream_shared_score,
        "data_confidence": data_confidence,
        "waiting_sla_score": waiting_sla_score
    }

    for input_name, input_value in normalized_inputs.items():

        if input_value is None:
            return {
                "status": "unknown",
                "reason": f"{input_name} is required for triage calculation"
            }

        if isinstance(input_value, bool) or not isinstance(
            input_value,
            (int, float, np.number)
        ):
            return {
                "status": "error",
                "reason": f"{input_name} must be numeric"
            }

        if not np.isfinite(input_value):
            return {
                "status": "error",
                "reason": f"{input_name} must be finite"
            }

        if not 0 <= input_value <= 1:
            return {
                "status": "error",
                "reason": f"{input_name} must be between 0 and 1"
            }

    # ---------------------------------------------------------
    # 4. التحقق من Revenue at Risk
    # ---------------------------------------------------------

    if revenue_at_risk_jod is None:
        return {
            "status": "unknown",
            "reason": "Revenue at Risk is required for triage calculation"
        }

    if (
        isinstance(revenue_at_risk_jod, bool)
        or not isinstance(
            revenue_at_risk_jod,
            (int, float, np.number)
        )
    ):
        return {
            "status": "error",
            "reason": "revenue_at_risk_jod must be numeric"
        }

    if (
        not np.isfinite(revenue_at_risk_jod)
        or revenue_at_risk_jod < 0
    ):
        return {
            "status": "error",
            "reason": "revenue_at_risk_jod must be a valid non-negative value"
        }

    # ---------------------------------------------------------
    # 5. التحقق من Revenue Reference
    # ---------------------------------------------------------

    if (
        isinstance(revenue_reference_jod, bool)
        or not isinstance(
            revenue_reference_jod,
            (int, float, np.number)
        )
    ):
        return {
            "status": "error",
            "reason": "revenue_reference_jod must be numeric"
        }

    if (
        not np.isfinite(revenue_reference_jod)
        or revenue_reference_jod <= 0
    ):
        return {
            "status": "error",
            "reason": "revenue_reference_jod must be greater than zero"
        }

    # ---------------------------------------------------------
    # 6. Normalize Technical Severity
    # ---------------------------------------------------------

    technical_normalized = (
        float(technical_severity) / 100.0
    )

    # ---------------------------------------------------------
    # 7. Normalize Revenue at Risk
    # ---------------------------------------------------------

    # revenue_reference_jod هي قيمة configurable
    # تمثل المبلغ الذي نعتبر عنده Revenue contribution وصل للحد الاقصى
    revenue_normalized = min(
        float(revenue_at_risk_jod)
        / float(revenue_reference_jod),
        1.0
    )

    # ---------------------------------------------------------
    # 8. حساب مساهمة كل عامل
    # ---------------------------------------------------------

    technical_contribution = (
        technical_normalized * 25
    )

    scope_contribution = (
        float(scope_ratio) * 20
    )

    revenue_contribution = (
        revenue_normalized * 20
    )

    recurrence_contribution = (
        float(recurrence_score) * 10
    )

    upstream_contribution = (
        float(upstream_shared_score) * 10
    )

    confidence_contribution = (
        float(data_confidence) * 10
    )

    waiting_contribution = (
        float(waiting_sla_score) * 5
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
        min(max(priority_score, 0.0), 100.0),
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

    if not isinstance(active_queue, list):
        return {
            "status": "error",
            "reason": "active_queue must be a list"
        }

    queue_scores = []

    for case in active_queue:

        if not isinstance(case, dict):
            return {
                "status": "error",
                "reason": "Each active_queue item must be a dictionary"
            }

        queue_case_id = case.get("case_id")
        queue_priority_score = case.get(
            "priority_score"
        )

        if queue_case_id is None:
            return {
                "status": "error",
                "reason": "Each queue case must contain case_id"
            }

        if queue_priority_score is None:
            return {
                "status": "error",
                "reason": "Each queue case must contain priority_score"
            }

        if (
            isinstance(queue_priority_score, bool)
            or not isinstance(
                queue_priority_score,
                (int, float, np.number)
            )
        ):
            return {
                "status": "error",
                "reason": "Queue priority_score must be numeric"
            }

        if not 0 <= queue_priority_score <= 100:
            return {
                "status": "error",
                "reason": "Queue priority_score must be between 0 and 100"
            }

        # اذا نفس الحالة موجودة في الـ queue
        # ما نضيف النسخة القديمة منها
        if str(queue_case_id) == str(case_id):
            continue

        queue_scores.append({
            "case_id": queue_case_id,
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
    # هي الي تكون قبلنا بالترتيب
    higher_priority_cases = sum(
        1
        for case in queue_scores
        if case["priority_score"] > priority_score
    )

    rank = higher_priority_cases + 1

    # ---------------------------------------------------------
    # 13. حساب Percentile
    # ---------------------------------------------------------

    queue_size = len(queue_scores)

    if queue_size == 1:
        percentile = 100.0

    else:
        lower_priority_cases = sum(
            1
            for case in queue_scores
            if case["priority_score"] < priority_score
        )

        percentile = (
            lower_priority_cases
            / (queue_size - 1)
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
                float(scope_ratio),
                4
            ),
            "revenue_at_risk": round(
                revenue_normalized,
                4
            ),
            "recurrence": round(
                float(recurrence_score),
                4
            ),
            "upstream_shared": round(
                float(upstream_shared_score),
                4
            ),
            "data_confidence": round(
                float(data_confidence),
                4
            ),
            "waiting_sla": round(
                float(waiting_sla_score),
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
            "upstream_shared": round(
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
            "upstream_shared": 10,
            "data_confidence": 10,
            "waiting_sla": 5
        },

        "revenue_reference_jod": float(
            revenue_reference_jod
        ),

        "limitations": [
            (
                "Scope and upstream/shared evidence must represent "
                "different signals to avoid double counting."
            ),
            (
                "Revenue normalization depends on the configured "
                "revenue_reference_jod."
            )
        ],

        "method_version": "triage_priority_v1"
    }