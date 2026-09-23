import pandas as pd
import numpy as np

def validate_reading_quality(readings_df: pd.DataFrame, value_col: str = 'reading') -> dict:
    """
    أداة لفحص جودة وموثوقية قراءات العدادات.
    """
    
    # 1. معالجة الحالات غير المدعومة (حسب معايير القبول لازم نرجع خطأ صريح)
    if readings_df is None or readings_df.empty or value_col not in readings_df.columns:
        return {
            "status": "error",
            "message": "Data is missing or unsupported"
        }
        
    values = readings_df[value_col]
    total_count = len(values)
        
    # 2. احسب عدد القيم المفقودة 
    missing_count = int(values.isna().sum())
    # 3. احسب عدد القيم السالبة (قراءات الاستهلاك مستحيل تكون تحت الصفر)
    negative_count = int((values < 0).sum())
    # 4. احسب عدد القيم الصفرية (Zero values)
    zero_count = int((values==0).sum())
    # 5. احسب عدد القراءات الثابتة اللي بتكرر ورا بعض (Flatlines)
    flatline_count = int((values.diff() == 0).sum())
    # 3. حساب علامة الجودة (من 100)
    # بنعمل وزن للمشاكل: السالبة أسوأ شي بنضربها بـ 5، المفقودة بـ 2
    penalty = (missing_count * 2) + (negative_count * 5) + (flatline_count * 0.5) + (zero_count * 1)
    
    # بنحسب العلامة النهائية وبنتأكد إنها ما تنزل تحت الصفر ولا تزيد عن 100
    quality_score = max(0.0, min(100.0, 100.0 - (penalty / total_count * 100)))
    
    if negative_count > 0 or missing_count > (total_count * 0.5):
        status = "unsupported_or_critical" 
    elif quality_score < 80:
        status = "warning"
    else:
        status = "valid"
    return {
        "status": status,
        "quality_score": round(quality_score, 2),
        "metrics": {
            "total_readings": total_count,
            "missing_values": missing_count,
            "negative_values": negative_count,
            "zero_values": zero_count,
            "flatline_values": flatline_count
        }
    }
def calculate_baseline(readings_df: pd.DataFrame, datetime_col: str = 'DateTime', value_col: str = 'reading') -> dict:
    """
    حساب خط الأساس للاستهلاك باستخدام إحصائيات قوية (الوسيط والمدى) 
    مجمعة حسب يوم الأسبوع ووقت اليوم.
    """
    # نأخذ نسخة عشان ما نعدل على الداتا الأصلية بالغلط
    df = readings_df.copy()
    
    # 1. تحويل عمود التاريخ لصيغة datetime اللي بيفهمها البانداس
    if not pd.api.types.is_datetime64_any_dtype(df[datetime_col]):
        df[datetime_col] = pd.to_datetime(df[datetime_col], errors='coerce')
    
    # 2. استخراج يوم الأسبوع (0=الاثنين, 6=الأحد) والساعة (0-23) في أعمدة جديدة
    df['day_of_week'] = df[datetime_col].dt.dayofweek
    df['hour'] = df[datetime_col].dt.hour
    
    # 3. تنظيف الداتا من القيم الفارغة قبل الحساب عشان ما تضرب المعادلات
    valid_data = df.dropna(subset=[value_col, 'day_of_week', 'hour'])
    
    if valid_data.empty:
        return {"status": "error", "message": "No valid data to calculate baseline", "baseline": {}}

    # 4. التجميع وحساب الإحصائيات القوية (Robust Statistics)
    # بنجمع الداتا حسب اليوم والساعة، وبنحسب الوسيط والربع الأول والثالث
    baseline_stats = valid_data.groupby(['day_of_week', 'hour'])[value_col].agg(
        median='median',
        q1=lambda x: x.quantile(0.25),
        q3=lambda x: x.quantile(0.75)
    ).reset_index()
    
    #حساب  (IQR = Q3 - Q1) 
    # هذا الرقم رح نستخدمه بالأداة الجاية عشان نحدد متى الهبوط يعتبر "'anomaly'"
    baseline_stats['iqr'] = baseline_stats['q3'] - baseline_stats['q1']
    
    # 5. (Dictionary) لتكون  النوع (Typed Output)
    baseline_dict = {}
    for _, row in baseline_stats.iterrows():
        # بنعمل مفتاح مميز لكل يوم وساعة (مثال: day_4_hour_14 يعني الجمعة الساعة 2 الظهر)
        key = f"day_{int(row['day_of_week'])}_hour_{int(row['hour'])}"
        baseline_dict[key] = {
            "median": round(row['median'], 3),
            "q1": round(row['q1'], 3),
            "q3": round(row['q3'], 3),
            "iqr": round(row['iqr'], 3)
        }
    
    return {
        "status": "success",
        "method": "time-of-day/day-of-week robust grouping",
        "baseline_profiles_count": len(baseline_dict),
        "profiles": baseline_dict
    }
def detect_anomaly(readings_df: pd.DataFrame, baseline_profiles: dict, datetime_col: str = 'DateTime', value_col: str = 'reading') -> dict:
    """
    اكتشاف anomaly بناءً على القواعد وحساب مقياس خطورة موحد من 0 إلى 100.
    """
    df = readings_df.copy()
    
    # تحويل عمود التاريخ لاستخراج اليوم والساعة للمطابقة مع خط الأساس
    if not pd.api.types.is_datetime64_any_dtype(df[datetime_col]):
        df[datetime_col] = pd.to_datetime(df[datetime_col], errors='coerce')
        
    df['day_of_week'] = df[datetime_col].dt.dayofweek
    df['hour'] = df[datetime_col].dt.hour
    
    # حساب القراءة السابقة لاكتشاف الثبات (Flatlines)
    df['prev_reading'] = df[value_col].shift(1)
    
    anomalies = []
    max_severity = 0.0
    
    for index, row in df.iterrows():
        val = row[value_col]
        dt = row[datetime_col]
        
        # استدعاء خط الأساس الخاص بهذا اليوم والساعة
        key = f"day_{int(row['day_of_week'])}_hour_{int(row['hour'])}"
        profile = baseline_profiles.get(key)
        
        if not profile:
            continue
            
        median = profile['median']
        iqr = profile['iqr']
        q1 = profile['q1']
        q3 = profile['q3']
        
        anomaly_type = None
        severity = 0.0
        components = {}
        
        # 1. اكتشاف الفجوات (Gaps)
        if pd.isna(val):
            anomaly_type = "gap"
            severity = 80.0
            components = {"reason": "missing_data"}
            
        # 2. اكتشاف فترات الصفر (Zero periods)
        elif val == 0:
            anomaly_type = "zero_period"
            severity = 100.0
            components = {"reason": "total_outage"}
            
        # 3. اكتشاف الثبات (Flatlines)
        elif val == row['prev_reading']:
            anomaly_type = "flatline"
            severity = 50.0
            components = {"reason": "sensor_stuck"}
            
        else:
            # استخدام IQR لتحديد الحدود max min (Rule-based)
            # تعديل الحماية 4: إضافة هامش تسامح بسيط للـ IQR لمنع فخ الإيجابيات الكاذبة (False Positives)
            # إذا كان IQR صفراً (الاستهلاك ثابت جداً)، أي تغير طفيف جداً سيعتبر شذوذاً بالغلط
            safe_iqr = max(iqr, 0.1) 
            
            lower_bound = q1 - (1.5 * safe_iqr)
            upper_bound = q3 + (1.5 * safe_iqr)
            
            # 4. اكتشاف  (Drops)
            if val < lower_bound:
                anomaly_type = "drop"
                # حساب الخطورة بناءً على نسبة الانحراف عن الوسيط
                deviation = (median - val) / median if median > 0 else 1
                severity = min(100.0, deviation * 100)
                components = {"deviation_pct": round(deviation * 100, 2), "limit": round(lower_bound, 2)}
                
            # 5. اكتشاف الارتفاع المفاجئ (Spikes)
            elif val > upper_bound:
                anomaly_type = "spike"
                deviation = (val - median) / median if median > 0 else 1
                severity = min(100.0, deviation * 100)
                components = {"deviation_pct": round(deviation * 100, 2), "limit": round(upper_bound, 2)}
                
        # تسجيل anomaly إذا تم اكتشافه
        if anomaly_type:
            max_severity = max(max_severity, severity)
            anomalies.append({
                "timestamp": str(dt),
                "type": anomaly_type,
                "reading_value": None if pd.isna(val) else val,
                "expected_median": median,
                "severity_score": round(severity, 2),
                "severity_components": components
            })
            
    # إرجاع outputs محددة النوع
    return {
        "status": "anomalies_detected" if anomalies else "normal",
        "total_anomalies": len(anomalies),
        "overall_severity_score": round(max_severity, 2),
        "detected_events": anomalies
    }