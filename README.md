# 🕵️‍♂️ MeterDetective

An advanced, AI-driven anomaly investigation agent for the Jordanian electricity grid. Built to identify, classify, and report power anomalies using robust statistics, graph network topology, and a local Large Language Model (Ollama).

## 🌟 Project Architecture

MeterDetective is not a simple rule-based script; it is a **20-tool Investigation Pipeline** that reasons about grid health by combining local meter data with global network context. 

The project is structured into progressive phases (Days):

### 🛠️ Day 1: Foundation & Contracts
- **Data Schema:** Defined the structure for consumption, topology, and customer metadata.
- **Investigation Schema:** Established the 18-Question standardized report format (`docs/investigation_report_schema.md`).
- **Metrics Specification:** Defined Revenue-at-Risk (JOD) and Triage Priority matrix (`docs/anomaly_metrics_spec.md`).

### 📊 Day 2: Data Pipeline & Topology (The Environment)
- **`prepare_dataset.py`:** Cleans raw LCL data and deterministically generates 150 synthetic meter profiles.
- **`generate_topology.py`:** Builds a hierarchical grid: `Substation -> 3 Feeders -> 6 Transformers -> 150 Meters` (25 meters per transformer).
- **`scenario_injector.py`:** Injects 10 complex anomalies (Local Drops, Shared Transformer Outages, Weather Spikes, EV Signatures) into a separate `ground_truth.csv` for evaluation.
- **`generate_fixtures.py`:** Provides JOD tariff data, customer DER metadata (Solar/EV), and historical alerts.
- **Automated Tests:** 10/10 `pytest` suite ensuring 100% data integrity.

### 🧠 Day 3: Core Analytics (The Senses)
- **`validate_reading_quality`:** Filters out impossible values, gaps, and flatlines.
- **`calculate_baseline`:** Computes robust expected behavior using Median and IQR (grouped by Day/Hour).
- **`detect_anomaly`:** Identifies sudden drops, spikes, and anomalies against the baseline.

### 🕸️ Day 4: Network Intelligence (The Reasoning)
- **`build_topology_graph`:** Uses `NetworkX` to construct the physical grid in memory.
- **`get_connected_assets`:** Identifies upstream transformers and sibling meters.
- **`select_dynamic_peers`:** Finds behaviorally similar meters for comparison.
- **`detect_shared_incident`:** Classifies incidents as **Local** (e.g., meter tampering) or **Shared** (e.g., transformer failure) by reasoning across neighbor data.

### 🤖 Day 5-10: Advanced AI & Agent Integration (Ongoing)
- **Agent Orchestration:** Powered by a locally hosted **Qwen 2.5 (via Ollama)** for 100% offline, secure, and resilient reasoning.
- **Hybrid Severity Engine:** Isolation Forest + Statistical Z-Scores.
- **Energy Balance:** Validating transformer input vs. downstream meter sum.

## 🚀 How to Run

### 1. Data Generation (Run Once)
```bash
python prepare_dataset.py
python generate_topology.py
python generate_fixtures.py
python scenario_injector.py
```

### 2. Run Tests
```bash
pytest test_day2.py -v
python test_data_tools.py
python test_day4_network.py
```

### 3. Start the Investigation Agent
Ensure `Ollama` is running in the background with the `qwen` model loaded.
```bash
python agent.py
```

---
*Developed for the AI Energy Hackathon - Team MeterDetective*