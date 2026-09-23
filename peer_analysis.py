import pandas as pd 
import numpy as np 
#عشان اعرف استهلاك العداد مقارنة بالعدادت لمجاورة بستدم هاي المكتبة
from scipy import stats

# tool to find similar meters according to meta data 
def select_dynamic_peers(target_meter_id, metadata_df, readings_df):
    #فلترت العدادات المتشابهة بالميتا داتا (عدلنا الكود ليتعامل مع الأعمدة الحقيقية)
    target_meta = metadata_df[metadata_df['Meter_ID'] == target_meter_id].iloc[0]
    similar_peers_meta = metadata_df[
        (metadata_df['Has_Solar'] == target_meta['Has_Solar']) &
        (metadata_df['Has_EV'] == target_meta['Has_EV']) &
        (metadata_df['Meter_ID'] != target_meter_id)
    ]['Meter_ID'].tolist()
    
    if not similar_peers_meta:
        return []
        
    relevant_meters = [target_meter_id] + similar_peers_meta
    filtered_readings = readings_df[readings_df['Meter_ID'].isin(relevant_meters)]
    pivot_df = filtered_readings.pivot(index='DateTime', columns='Meter_ID', values='KWH/hh (per half hour)')
    
    #اختار اعى خمس عدادات عشان احسب ال coorelation
    if target_meter_id in pivot_df.columns:
        #بحسب ال كو ريبشن مع العداد التارجت وبعدين بعمله دروب 
        correlations = pivot_df.corr()[target_meter_id].drop(target_meter_id)
        # بختار افضل خمس عدادات
        top_peers = correlations.nlargest(5).index.tolist()
        return top_peers
    return []


def compare_with_peers(target_meter_id, peers_list, readings_df, target_timestamp):
    """
    مقارنة قراءة العداد المستهدف مع قراءات العدادات المشابهة في وقت محدد
    """
    if not peers_list:
        return {"status": "unknown", "reason": "No peers found"}
        
    # غيرنا timestamp لـ DateTime عشان يطابق داتا Day 2
    time_filtered_df = readings_df[readings_df['DateTime'] == target_timestamp]

    # 1. سحب قراءة العداد المستهدف (Target)
    target_data = time_filtered_df[time_filtered_df['Meter_ID'] == target_meter_id]
    
    # تعديل الحماية 1: التأكد أن العداد له قراءة لتجنب خطأ IndexError وانهيار النظام
    if target_data.empty:
        return {"status": "error", "reason": "Target meter has no reading at this timestamp"}
        
    target_reading = target_data['KWH/hh (per half hour)'].iloc[0]

    # 2. سحب قراءات العدادات المجاورة (Peers)
    peers_readings = time_filtered_df[time_filtered_df['Meter_ID'].isin(peers_list)]['KWH/hh (per half hour)']
    
    # تعديل الحماية 2: التأكد أن الجيران لهم قراءات لتجنب احتساب متوسط فارغ (NaN)
    if peers_readings.empty:
        return {"status": "error", "reason": "Peers have no readings at this timestamp"}
        
    peers_mean = peers_readings.mean()
    
    # تعديل الحماية 3: منع القسمة على صفر (ZeroDivisionError) إذا كان متوسط استهلاك الجيران صفر
    if peers_mean > 0:
        deviation = (target_reading - peers_mean) / peers_mean * 100
    else:
        deviation = 100.0 if target_reading > 0 else 0.0
        
    percentile = stats.percentileofscore(peers_readings, target_reading)
    # 5. احسب النسبة المئوية (percentile) لاستهلاك العداد مقارنة بالجيران

    comparison_results = {
        "status": "answered",
        "peer_count": len(peers_readings),
        "target_value": round(float(target_reading), 2),    
        "peers_mean": round(float(peers_mean), 2),
        "deviation": round(float(deviation), 2),
        "percentile": round(float(percentile), 2) 
    }
    
    return comparison_results