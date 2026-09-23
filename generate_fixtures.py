import pandas as pd
import numpy as np
import json
import random


# ============================================================
# إعدادات
# ============================================================

TOPOLOGY_FILE = "topology.csv"

METADATA_FILE = "customer_metadata.csv"
TARIFF_FILE = "jod_tariff.json"
HISTORY_FILE = "historical_alerts.csv"

RANDOM_SEED = 42


# ============================================================
# تثبيت العشوائية
# ============================================================

np.random.seed(RANDOM_SEED)
random.seed(RANDOM_SEED)


print("=== Generating Fixtures ===")


# ============================================================
# 1. قراءة topology
# ============================================================

topology = pd.read_csv(
    TOPOLOGY_FILE
)

meters = (
    topology["Meter_ID"]
    .unique()
)

transformers = (
    topology["Transformer_ID"]
    .unique()
)


# ============================================================
# 2. Synthetic Customer / DER Metadata
# ============================================================

metadata_df = pd.DataFrame({

    "Meter_ID": meters,

    # Synthetic فقط
    "Has_Solar": np.random.choice(
        [True, False],
        size=len(meters),
        p=[0.15, 0.85]
    ),

    # Synthetic فقط
    "Has_EV": np.random.choice(
        [True, False],
        size=len(meters),
        p=[0.10, 0.90]
    ),

    "Customer_Type": "Residential",

    # حتى نكون واضحين أن هذه البيانات ليست observed
    "Data_Source": "synthetic"
})


metadata_df.to_csv(
    METADATA_FILE,
    index=False
)

print(
    f"Created: {METADATA_FILE}"
)


# ============================================================
# 3. JOD Tariff Fixture
# ============================================================

tariff_jod = {

    "currency": "JOD",

    "type": "Residential_Block",

    "version": "2026-v1",

    "source_type": "synthetic_demo_fixture",

    "brackets": [

        {
            "min_kwh": 0,
            "max_kwh": 300,
            "rate_per_kwh": 0.050
        },

        {
            "min_kwh": 301,
            "max_kwh": 600,
            "rate_per_kwh": 0.100
        },

        {
            "min_kwh": 601,
            "max_kwh": 999999,
            "rate_per_kwh": 0.200
        }
    ],

    "fixed_charges": 1.5
}


with open(
    TARIFF_FILE,
    "w"
) as f:

    json.dump(
        tariff_jod,
        f,
        indent=4
    )


print(
    f"Created: {TARIFF_FILE}"
)


# ============================================================
# 4. Historical Alerts / Closed Cases
# ============================================================

issue_types = [

    "Overheating",

    "Voltage_Drop",

    "Communication_Loss",

    "Meter_Malfunction",

    "Transformer_Mismatch"
]


historical_alerts = pd.DataFrame({

    "Ticket_ID": [
        f"TKT_{i+1000}"
        for i in range(20)
    ],

    "Equipment_ID": np.random.choice(
        transformers,
        size=20
    ),

    "Issue_Type": np.random.choice(
        issue_types,
        size=20
    ),

    "Date_Opened": pd.date_range(
        # خلينا التواريخ تبدأ من 2011 عشان تكون منطقية (سابقة لفترة التحقيق في 2012)
        start="2011-12-01",
        periods=20,
        freq="W"
    ).astype(str),

    "Status": "Closed",

    "Outcome": np.random.choice(
        [
            "Resolved",
            "Monitored",
            "Maintenance_Completed"
        ],
        size=20
    ),

    "Data_Source": "synthetic_fixture"
})


historical_alerts.to_csv(
    HISTORY_FILE,
    index=False
)


print(
    f"Created: {HISTORY_FILE}"
)


# ============================================================
# 5. Final summary
# ============================================================

print("\n=== Fixtures Generation Complete ===")

print(
    f"Meters: {len(meters)}"
)

print(
    f"Transformers: {len(transformers)}"
)

print(
    "Synthetic metadata: OK"
)

print(
    "Versioned JOD tariff: OK"
)

print(
    "Historical closed cases: OK"
)
