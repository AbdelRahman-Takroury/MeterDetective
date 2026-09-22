import pandas as pd
import json
from data_tools import validate_reading_quality, calculate_baseline, detect_anomaly

def generate_day3_deliverables():
    # 1. Prepare Normal Data (to teach the system the healthy baseline ~10.0)
    normal_data = pd.DataFrame({
        'DateTime': pd.date_range(start='2012-06-05 00:00', periods=5, freq='h'),
        'reading': [10.0, 10.2, 9.8, 10.1, 10.0]
    })
    
    # 2. Prepare sample data for Scenario 1 (Drop case)
    drop_data = pd.DataFrame({
        'DateTime': pd.date_range(start='2012-06-05 00:00', periods=5, freq='h'),
        'reading': [10.0, 10.2, 3.0, 3.1, 10.0] 
    })

    # 3. Run tools (Fix: Calculate baseline from normal history, then check drop data)
    quality = validate_reading_quality(drop_data)
    baseline = calculate_baseline(normal_data)
    anomaly_result = detect_anomaly(drop_data, baseline['profiles'])

    # 4. Save typed sample JSON outputs
    deliverables = {
        "tool3_quality_output": quality,
        "tool4_baseline_output": baseline,
        "tool5_anomaly_output": anomaly_result
    }
    
    with open('day3_sample_outputs.json', 'w', encoding='utf-8') as f:
        json.dump(deliverables, f, indent=4, ensure_ascii=False)
    
    print("✅ Sample JSON outputs successfully saved to 'day3_sample_outputs.json'")

    # 5. Populate questions 1-3 report fragment from deterministic results
    events = anomaly_result.get('detected_events', [])
    if events:
        drop_event = events[0]
        q1_answer = f"Yes, an anomaly was detected. Type: {drop_event['type'].upper()} at {drop_event['timestamp']}."
        q2_answer = f"The reading dropped to {drop_event['reading_value']}, while the expected normal baseline (median) was {drop_event['expected_median']}."
        q3_answer = f"The severity score is {drop_event['severity_score']}/100, calculated based on a {drop_event['severity_components'].get('deviation_pct', 0)}% deviation from the expected baseline."
    else:
        q1_answer = q2_answer = q3_answer = "No anomalies detected."

    report_fragment = f"""
=========================================
📝 METER DETECTIVE: INVESTIGATION REPORT
=========================================
Question 1: Was an anomaly detected in the provided data timeframe?
Answer: {q1_answer}

Question 2: What is the magnitude and nature of the anomaly?
Answer: {q2_answer}

Question 3: What is the severity score and how was it calculated?
Answer: {q3_answer}
=========================================
"""
    print("\n" + report_fragment)
    print("🎉 Day 3 tasks completed successfully! You are ready to push your code.")

if __name__ == "__main__":
    generate_day3_deliverables()