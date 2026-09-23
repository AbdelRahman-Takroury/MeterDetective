import pandas as pd
import numpy as np
from data_tools import validate_reading_quality, calculate_baseline, detect_anomaly

def run_all_tests():
    print("[START] Starting Unit Tests...\n")
    
    # 1. Normal Cases Test
    normal_data = pd.DataFrame({
        'DateTime': pd.date_range(start='2012-06-05 00:00', periods=5, freq='h'),
        'reading': [10.0, 10.2, 9.8, 10.1, 10.0]
    })
    
    quality = validate_reading_quality(normal_data)
    assert quality['status'] == 'valid', "[FAILED] Fail: Normal data was rejected!"
    
    baseline = calculate_baseline(normal_data)
    
    normal_result = detect_anomaly(normal_data, baseline['profiles'])
    assert normal_result['status'] == 'normal', "[FAILED] Fail: False anomaly detected in normal data!"
    print("[PASSED] Normal Case Test: Passed (No anomalies)")
    
    
    # 2. Drop Case Test (Scenario 1)
    drop_data = pd.DataFrame({
        'DateTime': pd.date_range(start='2012-06-05 00:00', periods=5, freq='h'),
        'reading': [10.0, 10.2, 3.0, 3.1, 10.0] 
    })
    drop_result = detect_anomaly(drop_data, baseline['profiles'])
    
    assert drop_result['status'] == 'anomalies_detected', "[FAILED] Fail: Drop was not detected!"
    drop_events = [e for e in drop_result['detected_events'] if e['type'] == 'drop']
    assert len(drop_events) > 0, "[FAILED] Fail: Drop was not classified correctly!"
    print("[PASSED] Drop Test: Passed (Sudden drop detected)")
    
    
    # 3. Gaps and Flatlines Test
    messy_data = pd.DataFrame({
        'DateTime': pd.date_range(start='2012-06-05 00:00', periods=4, freq='h'),
        'reading': [10.0, np.nan, 5.0, 5.0] 
    })
    messy_result = detect_anomaly(messy_data, baseline['profiles'])
    event_types = [e['type'] for e in messy_result['detected_events']]
    
    assert 'gap' in event_types, "[FAILED] Fail: Gap was not detected!"
    assert 'flatline' in event_types, "[FAILED] Fail: Flatline was not detected!"
    print("[PASSED] Gap/Flatline Test: Passed")

    print("\n[SUCCESS] Success! All tests passed. Code meets hackathon standards 100%.")

if __name__ == '__main__':
    run_all_tests()