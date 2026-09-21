# MeterDetective
This is a shared repository for the AI-agents Hackathon project: Meter Detective 
# 🕵️‍♂️ MeterDetective: AI-Powered Grid Analyzer

An autonomous AI Data Engineer Agent built for the hackathon to analyze electricity grid data, detect sudden consumption anomalies, and calculate financial impacts using the ReAct (Reasoning and Acting) framework.

## 🧠 System Architecture (ReAct Agent)
The core of MeterDetective is an autonomous agent powered by **Groq API (llama-3.3-70b-versatile)**. Instead of answering directly, the agent uses a thought loop to fetch real-world data, analyze it, and draw conclusions:
1. **Thought:** The agent understands the objective.
2. **Action:** It calls specific Python tools (`pandas`, `json`).
3. **Observation:** It reads the output of the tools.
4. **Final Answer:** It synthesizes a highly professional executive report.

## 🛠️ Available Tools (Function Calling)
- `get_transformer_data`: Fetches and aggregates half-hourly readings into daily averages to optimize LLM context limits (Tokens). Includes an injected 70% anomaly on `2012-06-05` for demo purposes.
- `get_tariff_info`: Retrieves Jordanian electricity tariff brackets (JOD).
- `get_historical_alerts`: Cross-references sudden drops with maintenance tickets (e.g., Voltage Drops, Tampering).

## 🗂️ Project Structure
- `agent.py`: The main ReAct Loop engine.
- `test_reproducibility.py`: Generates an MD5 hash of the dataset to prove 100% deterministic data generation (Hackathon Requirement).
- `transformer_readings.csv`: The aggregated consumption data.
- `jod_tariff.json`: Pricing brackets.
- `historical_alerts.csv`: Mocked SCADA/Maintenance logs.

## 🚀 How to Run
1. Install dependencies: `pip install pandas groq`
2. Add your API Key in `agent.py`
3. Run the Agent: `python agent.py`
4. Run the reproducibility test: `python test_reproducibility.py`