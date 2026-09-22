# Data Dictionary: Low Carbon London (Sample)

## Dataset Overview
- **Source**: Low Carbon London (LCL)
- **Sample File**: `LCL-June2015v2_98.csv`
- **Rows**: 500,000 (Evaluated sample)
- **Data Quality**: 0 missing values (Clean and ready for modeling)

## Features

| Feature | Data Type | Description |
| :--- | :--- | :--- |
| **LCLid** | String | Unique identifier for each household smart meter. |
| **stdorToU** | String | Tariff type indicator (Standard or Time of Use). |
| **DateTime** | Datetime | Timestamp of the reading, recorded at half-hour intervals. |
| **KWH/hh (per half hour)** | Float | Energy consumption measured in kilowatt-hours (kWh) per half hour. |