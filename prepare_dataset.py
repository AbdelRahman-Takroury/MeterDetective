import pandas as pd
import numpy as np


# ============================================================
# إعدادات المشروع
# ============================================================

INPUT_FILE = "LCL-June2015v2_98.csv"
OUTPUT_FILE = "seed_data.csv"

NUMBER_OF_METERS = 30
NUMBER_OF_DAYS = 30

# Seed ثابت حتى تكون عملية الـ synthetic augmentation قابلة لإعادة الإنتاج
RANDOM_SEED = 42

DATETIME_COL = "DateTime"
METER_COL = "LCLid"
VALUE_COL = "KWH/hh (per half hour)"


# ============================================================
# 1. قراءة البيانات
# ============================================================

print("=== Starting Dataset Preparation ===")

df = pd.read_csv(INPUT_FILE)

print(f"Original shape: {df.shape}")

# تنظيف أسماء الأعمدة من المسافات
df.columns = df.columns.str.strip()

required_columns = [DATETIME_COL, METER_COL, VALUE_COL]

# التأكد أن الأعمدة الأساسية موجودة
missing_columns = [
    col for col in required_columns
    if col not in df.columns
]

if missing_columns:
    raise ValueError(
        f"Missing required columns: {missing_columns}"
    )


# ============================================================
# 2. تحويل التاريخ
# ============================================================

df[DATETIME_COL] = pd.to_datetime(
    df[DATETIME_COL],
    errors="coerce"
)

# نحذف فقط الصفوف التي لا يمكن فيها قراءة التاريخ
df = df.dropna(subset=[DATETIME_COL])


# ============================================================
# 3. تحويل استهلاك الكهرباء إلى رقم
# ============================================================

df[VALUE_COL] = pd.to_numeric(
    df[VALUE_COL],
    errors="coerce"
)

# نحذف الصفوف التي لا تحتوي على قراءة رقمية
df = df.dropna(subset=[VALUE_COL])


# ============================================================
# 4. إزالة الـ duplicates
# ============================================================

before_duplicates = len(df)

df = df.drop_duplicates(
    subset=[METER_COL, DATETIME_COL]
)

removed_duplicates = before_duplicates - len(df)

print(f"Removed duplicate readings: {removed_duplicates}")


# ============================================================
# 5. فحص القراءات غير المنطقية
# ============================================================

negative_count = int(
    (df[VALUE_COL] < 0).sum()
)

print(f"Negative readings found: {negative_count}")

# استهلاك الكهرباء لا يجب أن يكون سالباً
df = df[df[VALUE_COL] >= 0]


# ============================================================
# 6. ترتيب البيانات زمنياً
# ============================================================

df = df.sort_values(
    by=[METER_COL, DATETIME_COL]
).reset_index(drop=True)


# ============================================================
# 7. اختيار 30 يوم من البيانات (يجب أن تكون قبل اختيار العدادات لضمان وجود بيانات لهم)
# ============================================================

start_date = df[DATETIME_COL].min()

end_date = (
    start_date
    + pd.Timedelta(days=NUMBER_OF_DAYS)
)

df = df[
    (df[DATETIME_COL] >= start_date)
    &
    (df[DATETIME_COL] < end_date)
].copy()

print(f"Start date: {start_date}")
print(f"End date:   {end_date}")


# ============================================================
# 8. اختيار 30 Meter
# ============================================================

unique_meters = df[METER_COL].dropna().unique()

if len(unique_meters) < NUMBER_OF_METERS:
    print(f"Warning: Only found {len(unique_meters)} meters with data in this date range. Adjusting target to {len(unique_meters)}.")
    actual_meters_to_select = len(unique_meters)
else:
    actual_meters_to_select = NUMBER_OF_METERS

selected_meters = unique_meters[:actual_meters_to_select]

df = df[
    df[METER_COL].isin(selected_meters)
].copy()

print(
    f"Selected meters: "
    f"{df[METER_COL].nunique()}"
)


# ============================================================
# 9. فحص الـ half-hour interval
# ============================================================

print("\n=== Interval Validation ===")

intervals = (
    df.groupby(METER_COL)[DATETIME_COL]
    .diff()
    .dropna()
)

half_hour_count = int(
    (intervals == pd.Timedelta(minutes=30)).sum()
)

total_intervals = len(intervals)

if total_intervals > 0:
    interval_consistency = (
        half_hour_count / total_intervals
    ) * 100
else:
    interval_consistency = 0.0

print(
    f"Half-hour intervals: "
    f"{interval_consistency:.2f}%"
)


# ============================================================
# 10. Synthetic augmentation
# ============================================================

print("\n=== Starting Synthetic Meter Generation ===")

rng = np.random.default_rng(RANDOM_SEED)

augmented_dfs = [df.copy()]
original_meters = df[METER_COL].unique()
current_count = len(original_meters)
target_count = 150
needed_synthetic = target_count - current_count

copy_index = 1
while needed_synthetic > 0:
    for meter in original_meters:
        if needed_synthetic == 0:
            break
            
        meter_data = df[df[METER_COL] == meter].copy()
        meter_data[METER_COL] = str(meter) + f"_Copy{copy_index}"
        
        noise_factor = rng.uniform(0.95, 1.05, size=len(meter_data))
        meter_data[VALUE_COL] = meter_data[VALUE_COL] * noise_factor
        meter_data[VALUE_COL] = meter_data[VALUE_COL].clip(lower=0)
        
        augmented_dfs.append(meter_data)
        needed_synthetic -= 1
        
    copy_index += 1

# دمج الأصل + النسخ synthetic
df_final = pd.concat(
    augmented_dfs,
    ignore_index=True
)


# ============================================================
# 11. ترتيب البيانات النهائية
# ============================================================

df_final = df_final.sort_values(
    by=[METER_COL, DATETIME_COL]
).reset_index(drop=True)


# ============================================================
# 12. تقرير نهائي
# ============================================================

print("\n=== Final Dataset Report ===")

print(
    f"Total meters: "
    f"{df_final[METER_COL].nunique()}"
)

print(
    f"Total readings: "
    f"{len(df_final)}"
)

print(
    f"Date range: "
    f"{df_final[DATETIME_COL].min()} "
    f"to "
    f"{df_final[DATETIME_COL].max()}"
)

print(
    f"Missing values:\n"
    f"{df_final.isnull().sum()}"
)

print(
    f"Negative readings: "
    f"{(df_final[VALUE_COL] < 0).sum()}"
)


# ============================================================
# 13. حفظ البيانات
# ============================================================

df_final.to_csv(
    OUTPUT_FILE,
    index=False
)

print(
    f"\nDataset saved successfully: "
    f"{OUTPUT_FILE}"
)
