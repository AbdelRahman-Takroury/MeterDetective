import pandas as pd
import numpy as np
file_path = 'LCL-June2015v2_98.csv'
df = pd.read_csv('LCL-June2015v2_98.csv')

# طباعة أول 5 أسطر للتأكد من القراءة
print(df.head())
df.columns = df.columns.str.strip()
print(df.columns)
df['DateTime'] = pd.to_datetime(df['DateTime'], errors='coerce') #حولت عمود الوقت لصيغة تاريخ ووقت حقيقين
df=df.dropna()
df=df.drop_duplicates()
print("\n=== Data After Cleaning ===")
print(df.info())
kwh_col = 'KWH/hh (per half hour)'
df[kwh_col] = pd.to_numeric(df[kwh_col], errors='coerce')
df=df.dropna()
print("\n=== Data After Numeric Conversion ===")
print(df.info())
#___________ substringsمرحلة اقتطاع البيانات ________
print("\n=== Starting Data Subsetting ===")
unique_meters = df['LCLid'].unique() #استخرجت اسماء العدادات من دون تكرار
selected_meters=unique_meters[:150]# جبت اول 150 واحد منهم 
df=df[df['LCLid'].isin(selected_meters)] #فلترت الداتا وخليت ال 150 عداد 
print(f"Meters selected: {len(selected_meters)}")
start_date=df['DateTime'].min()#جبت اول تاريخ بالداتا
end_date = start_date + pd.Timedelta(days=30)#زدت عليه 30 يوم
df = df[(df['DateTime'] >= start_date) & (df['DateTime'] <= end_date)]
print(f"Data filtered from {start_date} to {end_date}")
df = df[(df['DateTime'] >= start_date) & (df['DateTime'] <= end_date)] # فلترة الداتا الي بين تاريخ البداية وتاريخ النهاية 
print(f"Data filtered from {start_date} to {end_date}")
print("\n=== Final Subset Shape ===")
print(df.shape)
3. #Data Augmentation
print("\n=== Starting Data Augmentation ===")
augmented_dfs = [df]
for i in range(1, 5):
    df_copy = df.copy() #  أخذ نسخة منفصلة من الجدول عشان ما تخرب الداتا الاصلية عملت كوبي الها 
    
    # 1.  تغيير اسم العداد لضمان عدم التكرار
    df_copy['LCLid'] = df_copy['LCLid'] + f'_Copy{i}' # حطيت جنب كل اسم عشان اميزه كلمة كوبي و ال i الي هي بتعبر عن رقم النسخة
    


# عملت نويز  عشان مستقبلا لما ادرب الموديل عالداتا ما يصير overfitting + و حكينا لبايثون "ولّد لي أرقام عشوائية بعدد أسطر الداتا بالضبط".
    noise_factor = np.random.uniform(0.95, 1.05, size=len(df_copy))
    
    # 3. بمسك عامود الاستهلاك وبضربه بقيم الراندوم
    df_copy['KWH/hh (per half hour)'] = df_copy['KWH/hh (per half hour)'] * noise_factor
    
    # إضافة النسخة الجديدة للقائمة
    augmented_dfs.append(df_copy)

# دمج الجدول الأصلي مع كل النسخ في جدول واحد نهائي
df_final = pd.concat(augmented_dfs, ignore_index=True)

print(f"Total meters after augmentation: {df_final['LCLid'].nunique()}")
print("\n=== Final Augmented Subset Shape ===")
print(df_final.shape)

# ==========================================
# 4. حفظ البيانات النهائية
# ==========================================
df_final.to_csv('seed_data.csv', index=False)
print("Data saved successfully to seed_data.csv!")
