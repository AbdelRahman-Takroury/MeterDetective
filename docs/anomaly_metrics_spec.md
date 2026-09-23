# MeterDetective - Anomaly Metrics & Specifications

This document outlines the core business logic and mathematical formulas used by MeterDetective to evaluate anomalies.

## 1. Revenue at Risk (JOD)
Estimates the financial loss incurred during an anomaly (e.g., meter tampering or outage).

**Formula:**
`Lost_kWh = Baseline_Expected_kWh - Actual_Recorded_kWh`

If `Lost_kWh > 0`, calculate cost using the JOD Tariff Brackets:
- 0–300 kWh: 0.050 JOD/kWh
- 301–600 kWh: 0.100 JOD/kWh
- >600 kWh: 0.200 JOD/kWh

`Daily_Revenue_Loss = (Lost_kWh * Applicable_Rate) + Fixed_Charge_Loss (if applicable)`

## 2. Hybrid Severity Score (0-100)
A normalized score indicating the technical severity of the anomaly. It combines multiple components (Day 5 implementation).

**Components:**
- `Baseline_Deviation`: Distance from median in IQRs (Robust Z-score).
- `Duration_Penalty`: Increases if flatline or gap persists over multiple hours.
- `Peer_Deviation`: Distance from dynamic peer average.
- `Network_Spread`: Ratio of affected neighbors on the same transformer.

**Calculation:**
`Severity = min((Deviation_Weight * Baseline_Deviation) + (Peer_Weight * Peer_Deviation) + (Network_Weight * Network_Spread), 100)`

## 3. Triage Priority Matrix
Determines the urgency of field crew dispatch based on Severity and Revenue at Risk.

- **CRITICAL**: Severity > 85 OR Revenue Loss > 5.0 JOD/day. (Immediate dispatch)
- **HIGH**: Severity > 70 OR Shared Incident (Transformer level).
- **MEDIUM**: Severity > 40 AND Local Incident.
- **LOW**: Severity < 40 AND explained by Weather/DER. (Monitor only)
