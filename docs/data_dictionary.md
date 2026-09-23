# MeterDetective - Data Dictionary

This document defines the schema for the synthetic and physical data used by the MeterDetective anomaly detection engine.

## 1. `seed_data.csv` (Meter Readings)
Contains the half-hourly energy consumption readings for each meter.
- **DateTime**: `datetime` - The timestamp of the reading (30-minute intervals).
- **LCLid**: `string` - The unique identifier for the customer meter.
- **stdorToU**: `string` - Tariff type (Standard or Time of Use).
- **KWH/hh (per half hour)**: `float` - Energy consumed in kWh during the 30-minute interval.

## 2. `topology.csv` (Grid Network)
Defines the hierarchical structure of the power grid for graph traversal.
- **Meter_ID**: `string` - Unique meter identifier.
- **Transformer_ID**: `string` - The upstream transformer feeding the meter.
- **Feeder_ID**: `string` - The upstream feeder line.
- **Substation_ID**: `string` - The root substation.

## 3. `customer_metadata.csv` (DER & Behavior Context)
Provides contextual features used by the investigation agent to explain anomalies.
- **Meter_ID**: `string` - Unique meter identifier.
- **Has_Solar**: `boolean` - True if the property has solar PV installed.
- **Has_EV**: `boolean` - True if the property owns an electric vehicle.
- **Customer_Type**: `string` - E.g., 'Residential', 'Commercial'.
- **Data_Source**: `string` - Indicates synthetic nature.

## 4. `historical_alerts.csv` (Past Events)
Provides historical precedent for equipment issues.
- **Ticket_ID**: `string` - Unique ID for the historical alert.
- **Equipment_ID**: `string` - ID of the transformer or meter.
- **Issue_Type**: `string` - Type of fault (e.g., Overheating, Voltage_Drop).
- **Date_Opened**: `string` - When the issue occurred.
- **Status**: `string` - e.g., 'Closed'.
- **Outcome**: `string` - Resolution (e.g., 'Maintenance_Completed').

## 5. `transformer_readings.csv`
Downstream aggregated readings + technical loss at the transformer level.
- **Transformer_ID**: `string` - Transformer identifier.
- **DateTime**: `datetime` - Timestamp.
- **Transformer_Reading**: `float` - Total downstream kWh + 3% technical loss.

## 6. `ground_truth.csv` (Evaluation Only - Not visible to Agent)
Logs the injected synthetic anomalies to evaluate the Agent's accuracy.
- **Scenario_ID**: `string` - The injected scenario (e.g., SCN_001).
- **Timestamp**: `string` - When the anomaly was injected.
- **Target_ID**: `string` - Meter or Transformer ID.
- **Anomaly_Type**: `string` - Classification (e.g., Local-Drop, Shared-Drop, EV-Signature).
- **Scope**: `string` - 'Local', 'Shared', or 'Upstream'.
- **Expected_Effect**: `string` - Description of the modification.