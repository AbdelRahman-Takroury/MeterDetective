import pandas as pd
import numpy as np
import networkx as nx


# ============================================================
# بناء الـ Graph من topology.csv
# ============================================================

def build_topology_graph(topology_df):
    """
    بناء NetworkX graph من topology.csv.

    كل Meter مرتبط بـ Transformer.
    بنحدد نوع كل Node عشان نعرف لاحقاً
    هل هو Meter أو Transformer وما نحتاج نخمن.
    """

    G = nx.Graph()

    for row in topology_df.itertuples(index=False):

        # استخدمنا الأسماء الصحيحة لـ Day 2 (Capital Letters)
        meter = str(row.Meter_ID).strip()
        transformer = str(row.Transformer_ID).strip()

        # إضافة الـ Meter كـ Node وتعريف نوعه
        G.add_node(
            meter,
            asset_type="meter"
        )

        # إضافة الـ Transformer كـ Node وتعريف نوعه
        G.add_node(
            transformer,
            asset_type="transformer"
        )

        # ربط الـ Meter بالـ Transformer بـ Edge
        G.add_edge(
            meter,
            transformer
        )

    return G


# ============================================================
# استخراج الأصول المرتبطة بعداد معين
# ============================================================

def get_connected_assets(G, target_meter_id):
    """
    بنسحب المحول اللي عليه العداد، وبعدين كل العدادات الثانية 
    اللي على نفس المحول. هذه هي "الجيران" اللي رح نشوف عندهم نفس المشكلة ولا لأ.
    """

    if target_meter_id not in G:
        return {
            "status": "error",
            "reason": "Meter not in topology"
        }

    all_neighbors = list(G.neighbors(target_meter_id))

    transformer = None

    # بندور على جيران العداد ونلاقي المحول
    for neighbor in all_neighbors:

        # بنتحقق من الـ attribute عشان ما نشوف Meter ونظنه Transformer
        if G.nodes[neighbor].get("asset_type") == "transformer":
            transformer = neighbor
            break

    if transformer is None:
        return {
            "status": "error",
            "reason": "No transformer connected"
        }

    transformer_neighbors = list(G.neighbors(transformer))

    # بنجمع العدادات الثانية اللي على نفس المحول (بدون العداد اللي نحن نحقق فيه)
    connected_meters = []

    for neighbor in transformer_neighbors:

        if (
            G.nodes[neighbor].get("asset_type") == "meter"
            and neighbor != target_meter_id
        ):
            connected_meters.append(neighbor)

    return {
        "status": "success",
        "transformer": transformer,
        "connected_meters": connected_meters
    }


# ============================================================
# تحديد نوع الحادثة: Local أو Shared
# ============================================================

def detect_shared_incident(
    connected_meters,
    readings_df,
    target_timestamp,
    drop_threshold=0.40,
    lookback_periods=6,
    minimum_baseline_readings=3,
    shared_threshold=0.50
):
    """
    بنحدد هل الحادثة Local (عطل بعداد واحد) أو Shared (عطل بالمحول).

    المنطق الصحيح هون:
    ما بنحكم على العداد بأنه affected لأن قراءته أقل من 0.5 kWh.
    بل بنقارنه مع نفسه (baseline خاص بكل Meter).
    عشان لو عداد طبيعيًا يستهلك 0.3 kWh ما يصير نحكم إنه معطل.

    الخطوات:
    1. بنسحب القراءة الحالية لكل جار.
    2. بنحسب baseline من آخر N قراءات قبل الحادثة.
    3. بنحسب percentage_drop لكل جار.
    4. لو الانخفاض >= drop_threshold (40%) نعتبره affected.
    5. لو نسبة الجيران المتأثرين >= shared_threshold (50%) نحكم Shared.
    """

    # ---------------------------------------------------------
    # 1. فحص مدخلات أساسية قبل ما نبدأ
    # ---------------------------------------------------------

    if not connected_meters:
        return {
            "status": "unknown",
            "incident_type": "Unknown",
            "confidence": 0.0,
            "reason": "No connected meters available"
        }

    if readings_df is None or readings_df.empty:
        return {
            "status": "unknown",
            "incident_type": "Unknown",
            "confidence": 0.0,
            "reason": "Readings data is empty"
        }

    # ---------------------------------------------------------
    # 2. التأكد إن أسماء الأعمدة صح (Contract مع Day 2)
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # 3. تجهيز نسخة نظيفة من البيانات
    # ---------------------------------------------------------

    df = readings_df.copy()

    df["DateTime"] = pd.to_datetime(
        df["DateTime"],
        errors="coerce"
    )

    df["KWH/hh (per half hour)"] = pd.to_numeric(
        df["KWH/hh (per half hour)"],
        errors="coerce"
    )

    # بنشيل الصفوف الفاسدة عشان ما تأثر على الحسابات
    df = df.dropna(
        subset=[
            "Meter_ID",
            "DateTime",
            "KWH/hh (per half hour)"
        ]
    )

    # ---------------------------------------------------------
    # 4. تحويل الـ target_timestamp لـ Datetime
    # ---------------------------------------------------------

    target_timestamp = pd.to_datetime(
        target_timestamp,
        errors="coerce"
    )

    if pd.isna(target_timestamp):
        return {
            "status": "error",
            "reason": "Invalid target_timestamp"
        }

    # ---------------------------------------------------------
    # 5. سحب القراءة الحالية للجيران في لحظة الحادثة
    # ---------------------------------------------------------

    current_df = df[
        (df["Meter_ID"].isin(connected_meters)) &
        (df["DateTime"] == target_timestamp)
    ].copy()

    if current_df.empty:
        return {
            "status": "unknown",
            "incident_type": "Unknown",
            "confidence": 0.0,
            "reason": "No current readings found for connected meters at target_timestamp"
        }

    # ---------------------------------------------------------
    # 6. تحليل كل جار بشكل مستقل
    # ---------------------------------------------------------

    meter_results = []

    for meter_id in connected_meters:

        # القراءة الحالية لهذا الجار
        current_row = current_df[
            current_df["Meter_ID"] == meter_id
        ]

        if current_row.empty:
            meter_results.append({
                "meter_id": meter_id,
                "status": "unknown",
                "reason": "No current reading at target_timestamp"
            })
            continue

        current_value = float(
            current_row["KWH/hh (per half hour)"].iloc[0]
        )

        # سحب القراءات التاريخية قبل لحظة الحادثة
        historical_df = df[
            (df["Meter_ID"] == meter_id) &
            (df["DateTime"] < target_timestamp)
        ].sort_values("DateTime")

        # بنأخذ آخر N قراءة كأساس للـ baseline
        recent_history = historical_df.tail(lookback_periods)

        # لو ما عندنا كفاية قراءات تاريخية ما نقدر نحكم
        if len(recent_history) < minimum_baseline_readings:
            meter_results.append({
                "meter_id": meter_id,
                "status": "unknown",
                "current_value": round(current_value, 4),
                "reason": f"Only {len(recent_history)} historical readings (need {minimum_baseline_readings})"
            })
            continue

        # الـ baseline هو الـ Median وليس المتوسط عشان ما يتأثر بالـ Outliers
        baseline = float(
            recent_history["KWH/hh (per half hour)"].median()
        )

        # لو الـ baseline صفر ما بينفع نعمل قسمة
        if baseline <= 0:
            meter_results.append({
                "meter_id": meter_id,
                "status": "unknown",
                "current_value": round(current_value, 4),
                "baseline": round(baseline, 4),
                "reason": "Baseline is zero or negative (division not possible)"
            })
            continue

        # نسبة الانخفاض: (baseline - current) / baseline
        percentage_drop = (baseline - current_value) / baseline

        # بنعتبره affected إذا الانخفاض تجاوز الـ threshold المحدد
        affected = percentage_drop >= drop_threshold

        meter_results.append({
            "meter_id": meter_id,
            "status": "answered",
            "current_value": round(current_value, 4),
            "baseline": round(baseline, 4),
            "percentage_drop": round(percentage_drop * 100, 2),
            "affected": affected,
            "baseline_readings_used": len(recent_history)
        })

    # ---------------------------------------------------------
    # 7. بنحسب فقط على العدادات اللي عندنا evidence عنها
    # ---------------------------------------------------------

    answered_meters = [
        m for m in meter_results
        if m["status"] == "answered"
    ]

    # لو ما عندنا ولا جار نقدر نحكم على أساسه
    if not answered_meters:
        return {
            "status": "unknown",
            "incident_type": "Unknown",
            "confidence": 0.0,
            "reason": "Insufficient evidence across all connected meters",
            "meter_results": meter_results
        }

    # ---------------------------------------------------------
    # 8. حساب نسبة الجيران المتأثرين
    # ---------------------------------------------------------

    affected_count = sum(
        1 for m in answered_meters if m["affected"]
    )

    total_answered = len(answered_meters)

    affected_percentage = affected_count / total_answered

    # ---------------------------------------------------------
    # 9. قرار Shared أو Local
    # ---------------------------------------------------------

    if affected_percentage >= shared_threshold:
        # أكثر من نصف الجيران تأثروا = مشكلة بالمحول وليست بعداد واحد
        incident_type = "Shared"
        confidence = min(1.0, affected_percentage)
    else:
        # أقل من نصف الجيران تأثروا = المشكلة محلية
        incident_type = "Local"
        confidence = min(1.0, 1.0 - affected_percentage)

    # ---------------------------------------------------------
    # 10. النتيجة النهائية (traceable عشان الـ Agent يبني عليها)
    # ---------------------------------------------------------

    return {
        "status": "answered",
        "incident_type": incident_type,
        "affected_percentage": round(affected_percentage * 100, 2),
        "affected_count": affected_count,
        "total_answered_meters": total_answered,
        "drop_threshold_percentage": round(drop_threshold * 100, 2),
        "shared_threshold_percentage": round(shared_threshold * 100, 2),
        "confidence": round(confidence, 2),
        "meter_results": meter_results,
        "reason": (
            f"{affected_count} of {total_answered} connected meters "
            f"showed a drop of at least {drop_threshold * 100:.0f}% "
            f"from their individual recent baseline."
        )
    }