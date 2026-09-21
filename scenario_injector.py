import pandas as pd
import numpy as np

df_data=pd.read_csv('seed_data.csv')
print(df_data.columns)
df_topo=pd.read_csv('topology.csv')

#دمجت العدادات الي الي هم نفس الاشي بس اسمائهم مختلفة ب كل ملف دمجتهم عشان كل قراءة استهلاك يصير جنبها اسم المحول تبعها.
merged_df = pd.merge(df_data, df_topo, left_on='LCLid', right_on='Meter_ID', how='inner')
transformer_load = merged_df.groupby(['Transformer_ID','DateTime'])['KWH/hh (per half hour)'].sum().reset_index()

#ضربنا بـ 1.03 عشان نحاكي الواقع الفيزيائي لشبكات الكهرباء، واللي بنسميه " (Technical Loss)
transformer_load['Transformer_Reading'] = transformer_load['KWH/hh (per half hour)'] * 1.03

# anamly injection at day 15 with 70%percentage
transformer_load['Transformer_Reading'] = np.where(
    (transformer_load['DateTime'].str.contains('2013-01-15')) & (transformer_load['Transformer_ID'] == 'TX_3'),
    transformer_load['Transformer_Reading'] * 0.3,
    transformer_load['Transformer_Reading']
)
truth_df = pd.DataFrame({
    'Target_ID': ['TX_3'],
    'Anomaly_Date': ['2013-01-15'],
    'Anomaly_Type': ['Shared-Drop']
})

#حفظ الملفات 
transformer_load.to_csv('transformer_readings.csv', index=False)
truth_df.to_csv('ground_truth.csv', index=False)

print("=== Injection Complete ===")
print("Created: transformer_readings.csv (with technical loss & injected anomaly)")
print("Created: ground_truth.csv (hidden answers for the AI Agent)")