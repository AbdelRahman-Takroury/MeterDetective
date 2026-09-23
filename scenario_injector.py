import pandas as pd
import numpy as np

# ============================================================
# إعدادات
# ============================================================
INPUT_DATA = "seed_data.csv"
INPUT_TOPOLOGY = "topology.csv"
INPUT_METADATA = "customer_metadata.csv" # بنسحب الـ Metadata عشان ننقي العدادات الصح للسيناريوهات

OUTPUT_READINGS = "injected_readings.csv"
OUTPUT_TRANSFORMER = "transformer_readings.csv"
OUTPUT_GROUND_TRUTH = "ground_truth.csv"

RANDOM_SEED = 42

# ============================================================
# 1. قراءة البيانات (Data Ingestion)
# ============================================================
print("=== Starting Scenario Injection ===")

df = pd.read_csv(INPUT_DATA)
topology = pd.read_csv(INPUT_TOPOLOGY)
metadata = pd.read_csv(INPUT_METADATA)

# بنحول التاريخ لـ Datetime object عشان يسهل علينا الفلترة والـ Time-Series
df["DateTime"] = pd.to_datetime(df["DateTime"])

# ============================================================
# 2. ربط كل Meter بالمحول والميتاداتا (Data Merging)
# ============================================================
df = df.merge(
    topology[["Meter_ID", "Transformer_ID", "Feeder_ID", "Substation_ID"]],
    left_on="LCLid",
    right_on="Meter_ID",
    how="left"
)

# ============================================================
# 3. سجل الحقيقة (Ground Truth)
# بنعمله عشان نقيّم الـ Agent بالنهاية بدون ما نعطيه الداتا جاهزة يغش منها
# ============================================================
ground_truth = []

def add_ground_truth(scenario_id, timestamp, target_id, anomaly_type, scope, expected_effect):
    ground_truth.append({
        "Scenario_ID": scenario_id,
        "Timestamp": timestamp,
        "Target_ID": target_id,
        "Anomaly_Type": anomaly_type,
        "Scope": scope,
        "Expected_Effect": expected_effect
    })

# ============================================================
# Scenario 1 - Local Meter Drop
# ------------------------------------------------------------
# هون بنختبر قدرة النظام يصيد (Sudden Drop) بعداد واحد. 
# ممكن يكون سببه تلاعب (Tampering) أو عطل محلي. لازم الـ Agent يصنفه كـ Local Incident.
# ============================================================
target_meter = topology["Meter_ID"].iloc[0]
scenario_time = df["DateTime"].min() + pd.Timedelta(days=15)

mask = (df["Meter_ID"] == target_meter) & (df["DateTime"] >= scenario_time) & (df["DateTime"] < scenario_time + pd.Timedelta(hours=2))
df.loc[mask, "KWH/hh (per half hour)"] *= 0.30

add_ground_truth("SCN_001", str(scenario_time), target_meter, "Local-Drop", "Local", "70% consumption drop for one meter")


# ============================================================
# Scenario 2 - Communication Gap
# ------------------------------------------------------------
# عشان نختبر الـ Data Quality Tools وهل بتقدر تلاقي الـ Missing Values 
# بنعمل محاكاة لانقطاع الاتصال (Comms Failure) بين العداد والسيرفر لكم ساعة
# ============================================================
gap_meter = topology["Meter_ID"].iloc[1]
gap_time = df["DateTime"].min() + pd.Timedelta(days=16)

gap_mask = (df["Meter_ID"] == gap_meter) & (df["DateTime"] >= gap_time) & (df["DateTime"] < gap_time + pd.Timedelta(hours=6))
df.loc[gap_mask, "KWH/hh (per half hour)"] = np.nan

add_ground_truth("SCN_002", str(gap_time), gap_meter, "Communication-Gap", "Local", "6-hour missing-reading window")


# ============================================================
# Scenario 3 - Flatline
# ------------------------------------------------------------
# اختبار خوارزمية الـ IQR وحمايتها من مشكلة الـ ZeroDivisionError
# بنعلق العداد (Stuck Meter) وبنخليه يرسل نفس القراءة بدون أي Variance
# ============================================================
flatline_meter = topology["Meter_ID"].iloc[2]
flatline_time = df["DateTime"].min() + pd.Timedelta(days=17)

flatline_mask = (df["Meter_ID"] == flatline_meter) & (df["DateTime"] >= flatline_time) & (df["DateTime"] < flatline_time + pd.Timedelta(hours=4))
flatline_values = df.loc[flatline_mask, "KWH/hh (per half hour)"]

if not flatline_values.empty:
    fixed_value = flatline_values.iloc[0]
    df.loc[flatline_mask, "KWH/hh (per half hour)"] = fixed_value

add_ground_truth("SCN_003", str(flatline_time), flatline_meter, "Flatline", "Local", "Repeated identical readings")


# ============================================================
# Scenario 4 - Shared Drop
# ------------------------------------------------------------
# اختبار لكود الـ shared incident في Day 4
# بنعمل محاكاة لعطل بالمحول (Transformer Outage) بوقع قراءات أكثر من 50% من العدادات اللي عليه
# ============================================================
shared_transformer = topology["Transformer_ID"].iloc[0]
shared_meters = topology[topology["Transformer_ID"] == shared_transformer]["Meter_ID"].tolist()
shared_time = df["DateTime"].min() + pd.Timedelta(days=18)

shared_mask = df["Meter_ID"].isin(shared_meters) & (df["DateTime"] >= shared_time) & (df["DateTime"] < shared_time + pd.Timedelta(hours=2))
df.loc[shared_mask, "KWH/hh (per half hour)"] *= 0.60

add_ground_truth("SCN_004", str(shared_time), shared_transformer, "Shared-Drop", "Shared", "40% drop across meters under one transformer")


# ============================================================
# Scenario 5 - Transformer Mismatch
# ------------------------------------------------------------
# عشان نختبر הـ Energy Balance في Day 5
# رح نعمل فرق بين قراءة المحول (Upstream) ومجموع العدادات (Downstream) 
# عشان نمثل تسريب أو سرقة كهرباء مش مفوترة (Non-Technical Loss)
# ============================================================
mismatch_transformer = topology["Transformer_ID"].iloc[1]
mismatch_time = df["DateTime"].min() + pd.Timedelta(days=19)

add_ground_truth("SCN_005", str(mismatch_time), mismatch_transformer, "Transformer-Mismatch", "Upstream", "Transformer input differs from downstream total")


# ============================================================
# Scenario 6 - Weather-Explained Spike
# ------------------------------------------------------------
# عشان نتأكد إن الـ Agent ما بيستعجل ويحسب أي ارتفاع استهلاك على إنه عطل (False Positive)
# بنمثل ارتفاع طبيعي بسبب موجة حر أو برد، والـ Agent المفروض يطلب أداة الطقس ليبرره
# ============================================================
weather_meter = topology["Meter_ID"].iloc[3]
weather_time = df["DateTime"].min() + pd.Timedelta(days=20)

weather_mask = (df["Meter_ID"] == weather_meter) & (df["DateTime"] >= weather_time) & (df["DateTime"] < weather_time + pd.Timedelta(hours=3))
df.loc[weather_mask, "KWH/hh (per half hour)"] *= 1.50

add_ground_truth("SCN_006", str(weather_time), weather_meter, "Weather-Explained-Spike", "Local", "50% load increase explained by synthetic weather context")


# ============================================================
# Scenario 7 - Evening EV Signature
# ------------------------------------------------------------
# بنختبر قدرة الـ Agent يقرأ الـ Metadata ويبرر الـ Spike
# بنرفع الاستهلاك فجأة المساء (18:00)، والـ Agent الذكي رح يشوف إن العداد عنده سيارة كهربائية (Has_EV = True)
# ============================================================
# بنفلتر الداتا عشان نختار عداد عنده EV فعلياً ونتجنب أي Data Contradiction
ev_meters = metadata[metadata["Has_EV"] == True]["Meter_ID"].tolist()
ev_meter = ev_meters[0] if ev_meters else topology["Meter_ID"].iloc[4]

# بنضبط التوقيت ليكون Evening بشكل واقعي
ev_time = df["DateTime"].min() + pd.Timedelta(days=21)
ev_time = ev_time.replace(hour=18, minute=0, second=0)

ev_mask = (df["Meter_ID"] == ev_meter) & (df["DateTime"] >= ev_time) & (df["DateTime"] < ev_time + pd.Timedelta(hours=3))
df.loc[ev_mask, "KWH/hh (per half hour)"] *= 1.80

add_ground_truth("SCN_007", str(ev_time), ev_meter, "EV-Signature", "Local", "Evening load increase consistent with EV scenario")


# ============================================================
# Scenario 8 - Solar PV Net Load Drop
# ------------------------------------------------------------
# نفس فكرة الـ EV بس هون بنختبر تبرير انخفاض الاستهلاك بالـ Net Load وقت الظهر (12:00) 
# الـ Agent لازم يبرره بإن العميل عنده خلايا شمسية (Has_Solar = True) بتنتج وقت الذروة
# ============================================================
# بنختار عداد مركب طاقة شمسية
solar_meters = metadata[metadata["Has_Solar"] == True]["Meter_ID"].tolist()
solar_meter = solar_meters[0] if solar_meters else topology["Meter_ID"].iloc[5]

# بنثبت التوقيت ليكون Midday وقت سطوع الشمس القوي
solar_time = df["DateTime"].min() + pd.Timedelta(days=22)
solar_time = solar_time.replace(hour=12, minute=0, second=0)

solar_mask = (df["Meter_ID"] == solar_meter) & (df["DateTime"] >= solar_time) & (df["DateTime"] < solar_time + pd.Timedelta(hours=3))
df.loc[solar_mask, "KWH/hh (per half hour)"] *= 0.50

add_ground_truth("SCN_008", str(solar_time), solar_meter, "Solar-Net-Load-Drop", "Local", "Midday net-load reduction consistent with solar PV")


# ============================================================
# Scenario 9 - Temporary Customer Behavior
# ------------------------------------------------------------
# بنعمل Outlier مؤقت لا هو جريمة ولا عطل (مثلاً العميل شغل مكيفات إضافية)
# بنختبر ذكاء الـ Agent بتمييز الأنماط المؤقتة (Behavioral Change)
# ============================================================
behavior_meter = topology["Meter_ID"].iloc[6]
behavior_time = df["DateTime"].min() + pd.Timedelta(days=23)

behavior_mask = (df["Meter_ID"] == behavior_meter) & (df["DateTime"] >= behavior_time) & (df["DateTime"] < behavior_time + pd.Timedelta(hours=5))
df.loc[behavior_mask, "KWH/hh (per half hour)"] *= 1.25

add_ground_truth("SCN_009", str(behavior_time), behavior_meter, "Customer-Behavior-Change", "Local", "Temporary internally consistent load schedule change")


# ============================================================
# Scenario 10 - Post-Action Recurrence
# ------------------------------------------------------------
# بنختبر استغلال الـ Agent للتاريخ (Historical Precedent)
# بنعمل عطل، وبنرجعه للطبيعي، وبعد 12 ساعة بنعمل عطل جديد (Recurrence) 
# عشان نبين إنه الحل اللي صار بالـ Field ما كان جذري 
# ============================================================
recurrence_meter = topology["Meter_ID"].iloc[7]
recurrence_time_1 = df["DateTime"].min() + pd.Timedelta(days=24) # العطل الأول (Initial Anomaly)
recurrence_time_2 = recurrence_time_1 + pd.Timedelta(hours=12) # تكرار العطل بعد نص يوم

# بنحقن العطل الأول
mask_1 = (df["Meter_ID"] == recurrence_meter) & (df["DateTime"] >= recurrence_time_1) & (df["DateTime"] < recurrence_time_1 + pd.Timedelta(hours=2))
df.loc[mask_1, "KWH/hh (per half hour)"] *= 0.30

# بنحقن التكرار تبعه
mask_2 = (df["Meter_ID"] == recurrence_meter) & (df["DateTime"] >= recurrence_time_2) & (df["DateTime"] < recurrence_time_2 + pd.Timedelta(hours=2))
df.loc[mask_2, "KWH/hh (per half hour)"] *= 0.30

add_ground_truth("SCN_010", str(recurrence_time_2), recurrence_meter, "Post-Action-Recurrence", "Local", "Condition returns after simulated action window")


# ============================================================
# Transformer readings (Energy Balance)
# ============================================================
print("\n=== Generating Transformer Readings ===")

# بنجمع قراءات كل العدادات اللي على نفس المحول (Downstream Aggregation) وبنضيف 3% كـ Technical Loss منطقي بالشبكة
transformer_downstream = df.groupby(["Transformer_ID", "DateTime"])["KWH/hh (per half hour)"].sum().reset_index()
transformer_downstream["Transformer_Reading"] = transformer_downstream["KWH/hh (per half hour)"] * 1.03

# تعديل قراءة المحول الخاص بـ Scenario 5 عشان نمثل تسريب الكهربا
mismatch_mask = (transformer_downstream["Transformer_ID"] == mismatch_transformer) & (transformer_downstream["DateTime"] >= mismatch_time) & (transformer_downstream["DateTime"] < mismatch_time + pd.Timedelta(hours=2))
transformer_downstream.loc[mismatch_mask, "Transformer_Reading"] *= 1.30

# ============================================================
# حفظ الملفات (Export Artifacts)
# ============================================================
df.to_csv(OUTPUT_READINGS, index=False)
transformer_downstream.to_csv(OUTPUT_TRANSFORMER, index=False)
pd.DataFrame(ground_truth).to_csv(OUTPUT_GROUND_TRUTH, index=False)

print("\n=== Scenario Injection Complete ===")