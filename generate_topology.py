import pandas as pd
import numpy as np


# ============================================================
# إعدادات
# ============================================================

INPUT_FILE = "seed_data.csv"
OUTPUT_FILE = "topology.csv"

RANDOM_SEED = 42

SUBSTATION_ID = "Sub_Main_1"

TRANSFORMERS = [
    "TX_1",
    "TX_2",
    "TX_3",
    "TX_4",
    "TX_5",
    "TX_6"
]

FEEDER_MAP = {
    "TX_1": "Feeder_1",
    "TX_2": "Feeder_1",

    "TX_3": "Feeder_2",
    "TX_4": "Feeder_2",

    "TX_5": "Feeder_3",
    "TX_6": "Feeder_3"
}


# ============================================================
# 1. قراءة الـ seed data
# ============================================================

print("=== Starting Topology Generation ===")

df = pd.read_csv(INPUT_FILE)

meters = (
    df["LCLid"]
    .dropna()
    .unique()
    .tolist()
)

print(
    f"Meters found: {len(meters)}"
)


# ============================================================
# 2. تثبيت العشوائية
# ============================================================

rng = np.random.default_rng(
    RANDOM_SEED
)

# نخلط العدادات حتى لا يكون توزيعها
# مرتبطاً بترتيب البيانات الأصلي
rng.shuffle(meters)


# ============================================================
# 3. توزيع العدادات على المحولات
# ============================================================

meter_count = len(meters)
transformer_count = len(TRANSFORMERS)

# نوزع العدادات بالتساوي قدر الإمكان
transformer_assignments = np.resize(
    TRANSFORMERS,
    meter_count
)


# ============================================================
# 4. إنشاء topology records
# ============================================================

topology_rows = []

for meter_id, transformer_id in zip(
    meters,
    transformer_assignments
):

    feeder_id = FEEDER_MAP[
        transformer_id
    ]

    topology_rows.append({
        "Meter_ID": meter_id,
        "Transformer_ID": transformer_id,
        "Feeder_ID": feeder_id,
        "Substation_ID": SUBSTATION_ID
    })


topology_df = pd.DataFrame(
    topology_rows
)


# ============================================================
# 5. التحقق من topology
# ============================================================

print("\n=== Topology Validation ===")

print(
    f"Total meters: "
    f"{topology_df['Meter_ID'].nunique()}"
)

print(
    f"Total transformers: "
    f"{topology_df['Transformer_ID'].nunique()}"
)

print(
    f"Total feeders: "
    f"{topology_df['Feeder_ID'].nunique()}"
)


# عدد العدادات لكل Transformer
meters_per_transformer = (
    topology_df
    .groupby("Transformer_ID")
    .size()
)

print("\nMeters per transformer:")
print(
    meters_per_transformer
)


# ============================================================
# 6. التأكد من عدم وجود Meter مكرر
# ============================================================

duplicate_meters = (
    topology_df["Meter_ID"]
    .duplicated()
    .sum()
)

if duplicate_meters > 0:
    raise ValueError(
        "Duplicate meters found in topology."
    )


# ============================================================
# 7. حفظ topology
# ============================================================

topology_df.to_csv(
    OUTPUT_FILE,
    index=False
)

print(
    f"\nTopology saved successfully: "
    f"{OUTPUT_FILE}"
)

print("\nSample:")
print(
    topology_df.head(15)
)