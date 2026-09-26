# Frozen Day 1 contracts

Contract version: `1.0`. Breaking changes require a deliberate version increment and migration.
The executable Pydantic definitions live in `app/contracts/` and reject unknown fields.

## Event envelope

Every event has a UUID, event type, occurrence timestamp, source, schema version, scoped meter
IDs and/or transformer ID, and a typed event-specific `data` object. Supported initial event types
are reading received, anomaly detected, investigation requested, approval decided, action completed,
and verification requested. Events without an asset scope are invalid.

## Tool execution envelope

Every tool uses typed input and output models. The common execution record contains `run_id`,
`tool_name`, input, optional output, `status`, non-negative `latency_ms`, and a structured optional
error (`code`, safe message, retryability, details). Outputs carry evidence references and warnings.
Evidence records identify source, kind, time, reliability from 0–1, and metadata.

The frozen tool registry is:

1. `get_meter_profile`
2. `get_reading_window`
3. `validate_reading_quality`
4. `calculate_baseline`
5. `detect_anomaly`
6. `forecast_expected_usage`
7. `select_dynamic_peers`
8. `compare_with_peers`
9. `get_connected_assets`
10. `detect_shared_incident`
11. `calculate_energy_balance`
12. `get_weather_context`
13. `analyze_customer_der_context`
14. `search_technical_knowledge`
15. `find_meter_precedents`
16. `estimate_revenue_at_risk`
17. `calculate_triage_priority`
18. `create_case_and_record_investigation`
19. `propose_action_for_approval`
20. `verify_case_outcome`

## Agent state

`AgentState` preserves the run/case IDs, trigger, affected meters, goal, facts, competing
hypotheses and their evidence, completed/failed tool calls, missing evidence, next step, approval,
verification, termination, report link, financial impact, precedents, triage, and update time.
This object is checkpoint-safe JSON stored on `agent_runs.state_json`.

## Investigation report

Every abnormal event produces a versioned report containing exactly these question IDs once:

1. What happened?
2. How abnormal is it?
3. Is the data itself reliable?
4. Is this meter alone affected?
5. Are nearby or related meters affected?
6. Can weather explain the change?
7. Could customer behavior explain it?
8. Could solar, EV, or other load changes explain it?
9. Is there an upstream transformer or feeder mismatch?
10. What are the most plausible hypotheses?
11. How confident is the system?
12. What evidence supports or contradicts each hypothesis?
13. What should happen next?
14. Does the action require human approval?
15. Did the condition improve afterward?
16. What is the expected Revenue at Risk in JOD?
17. Are there meter precedents, repeated patterns, or closed cases?
18. What is its triage priority against all active alerts?

Each answer includes an explicit status (`answered`, `unknown`, `not_applicable`, or
`pending_verification`), prose, structured values/units, confidence, supporting and contradicting
evidence, tools and sources, freshness, and limitations. An answered item requires supporting
evidence. Any other state requires a limitation. Before follow-up data exists, question 15 is
`pending_verification`.

The validator rejects missing or duplicate question IDs and unsupported answers. `unknown` is
valid and preferable to an invented value. Revenue at Risk is JOD-only for this sprint, requires
ordered low/base/high values, tariff version, assumptions, and confidence. Triage requires a
0–100 score, P1–P4 band, active rank/count, factor breakdown, and policy version.

