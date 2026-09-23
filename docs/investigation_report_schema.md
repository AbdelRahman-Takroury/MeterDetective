# MeterDetective - 18-Question Investigation Report Schema

When the MeterDetective Agent successfully completes an anomaly investigation, it must output a structured JSON report answering the following 18 standardized questions. This schema ensures all investigations are comprehensive, consistent, and machine-readable, focusing on acting as an **Investigation Assistant**, not a fraud detector.

## Schema Structure

```json
{
  "investigation_id": "UUID",
  "target_asset": "METER_OR_TRANSFORMER_ID",
  "anomaly_timestamp": "YYYY-MM-DD HH:MM:SS",
  
  "Q1_what_happened": "What is the primary classification? (Drop, Spike, Flatline, Gap)",
  "Q2_how_abnormal": "What is the hybrid severity score (0-100)?",
  "Q3_is_data_reliable": "Are there missing values or intervals affecting the baseline?",
  "Q4_is_meter_alone": "Is this meter alone affected? (Local vs Shared)",
  "Q5_are_nearby_affected": "Are neighboring meters on the same transformer experiencing anomalies?",
  
  "Q6_baseline_behavior": "What is the expected normal consumption for this time?",
  "Q7_deviation_magnitude": "How far did the actual reading deviate from the baseline?",
  "Q8_duration": "How long has this anomaly persisted?",
  "Q9_time_of_day_context": "Is the anomaly associated with a specific time of day?",
  
  "Q10_peer_comparison": "How does this behavior compare to dynamically selected peers?",
  "Q11_transformer_balance": "Is the upstream transformer input matching the downstream meter sum?",
  
  "Q12_weather_correlation": "Can this anomaly be explained by recent weather events?",
  "Q13_der_context": "Is the customer known to have Solar PV or EV chargers that explain this pattern?",
  "Q14_historical_precedent": "Are there historical closed tickets for this asset before the event?",
  
  "Q15_revenue_at_risk": "What is the estimated financial loss in JOD?",
  "Q16_root_cause_hypothesis": "What is the primary hypothesis (e.g. Technical fault, communication loss, behavior)?",
  "Q17_recommended_action": "What is the immediate next step for the field team?",
  "Q18_triage_priority": "What is the triage priority vs active alerts? (Critical, High, Medium, Low)"
}
```
