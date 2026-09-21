import pandas as pd
import numpy as np
import json
import random

print("=== Generating Fixtures ===")

# قراءة خريطة الشبكة عشان نربط البيانات بالعدادات والمحولات الفعلية
df_topo = pd.read_csv('topology.csv')
meters = df_topo['Meter_ID'].unique()
transformers = df_topo['Transformer_ID'].unique()

# تثبيت العشوائية (Seed) مهم جداً عشان نحقق شرط: 
# Identical seed produces identical data
np.random.seed(42)
random.seed(42)

# 1. توليد بيانات العملاء الوهمية (Metadata)
# بنعطي احتمالية 15% يكون عند العميل طاقة شمسية، و 10% سيارة كهربائية
metadata_df = pd.DataFrame({
    'Meter_ID': meters,
    'Has_Solar': np.random.choice([True, False], size=len(meters), p=[0.15, 0.85]),
    'Has_EV': np.random.choice([True, False], size=len(meters), p=[0.10, 0.90]),
    'Customer_Type': 'Residential'
})
metadata_df.to_csv('customer_metadata.csv', index=False)
print("Created: customer_metadata.csv (Solar and EV assignments)")

# 2. تعرفة الكهرباء الأردنية (JOD Tariff)
# شرائح تقريبية للاستهلاك المنزلي بالدينار الأردني
tariff_jod = {
    "currency": "JOD",
    "type": "Residential_Block",
    "version": "2026-v1",
    "brackets": [
        {"min_kwh": 0, "max_kwh": 300, "rate_per_kwh": 0.050},
        {"min_kwh": 301, "max_kwh": 600, "rate_per_kwh": 0.100},
        {"min_kwh": 601, "max_kwh": 999999, "rate_per_kwh": 0.200}
    ],
    "fixed_charges": 1.5
}
with open('jod_tariff.json', 'w') as f:
    json.dump(tariff_jod, f, indent=4)
print("Created: jod_tariff.json (Jordanian electricity brackets)")

# 3. التنبيهات التاريخية (Historical Alerts)
issue_types = ['Overheating', 'Voltage Drop', 'Communication Loss', 'Tampering_Suspected']

# الفكرة هون قريبة جداً من منطق أنظمة تصعيد تذاكر الدعم الفني (IT support ticket escalation) 
# المربوطة بنماذج التنبؤ اللي بتنعمل بـ Flask، بس هون رح نولد جدول بسيط لـ 15 تذكرة صيانة مغلقة
historical_alerts = pd.DataFrame({
    'Ticket_ID': [f"TKT_{i+1000}" for i in range(15)],
    'Equipment_ID': np.random.choice(transformers, size=15),
    'Issue_Type': np.random.choice(issue_types, size=15),
    'Date_Opened': pd.date_range(start='2012-10-01', periods=15, freq='W').astype(str),
    'Status': 'Closed'
})
historical_alerts.to_csv('historical_alerts.csv', index=False)
print("Created: historical_alerts.csv (Seed history of closed cases)")

print("=== Fixtures Generation Complete ===")
