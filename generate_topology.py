import pandas as pd
import numpy as np
print("=== Starting Topology Generation ===")
df=pd.read_csv('seed_data.csv')
meters = df['LCLid'].unique().tolist()
print(f"Total meters found: {len(meters)}")
transformers = ['TX_1', 'TX_2', 'TX_3', 'TX_4', 'TX_5', 'TX_6']
#every fedeer links with 2 transformers 
feeders_map={
    'TX_1':'Feeder_1','TX_2':'Feeder_1',
    'TX_3':'Feeder_2','TX_4':'Feeder_2',
    'TX_5':'Feeder_3','TX_6':'Feeder_3',
}
# 1. خلط العدادات عشوائياً عشان نكسر ترتيب النسخ
np.random.shuffle(meters)

# 2حساب عدد التكرارات المطلوبة لكل محول
repeats = (len(meters) // len(transformers)) + 1 

# 3. توزيع العدادات على شكل "بلوكات" 
assigned_tx = np.repeat(transformers, repeats)[:len(meters)]
# 5. بناء جدول الشبكة
topology_df = pd.DataFrame({
    'Meter_ID': meters,
    'Transformer_ID': assigned_tx
})

# 6. ربط المحولات بال feedeers  باستخدام القاموس
topology_df['Feeder_ID'] = topology_df['Transformer_ID'].map(feeders_map)
#Substation (محطة التحويل): المركز الرئيسي اللي بيغذي كل منطقتنا
topology_df['Substation_ID'] = 'Sub_Main_1'
# 7. حفظت الملف 
topology_df.to_csv('topology.csv', index=False)

print("\n=== Topology Created Successfully ===")
print(topology_df.head(15)) # طبعنا 15 سطر عشان تشوف البلوكات كيف ترتبت
print(f"\nTopology saved to topology.csv with {len(topology_df)} meters.")