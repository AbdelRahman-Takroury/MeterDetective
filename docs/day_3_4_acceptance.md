# Day 3/4 integrated acceptance

## Implemented capabilities

- Tools 1–5, 7–10, 15, and 18 are registered under their canonical names.
- Every execution validates typed input/output, records latency, success/failure, safe error details,
  and evidence metadata in `tool_executions`.
- `POST /api/replay/step` runs the deterministic investigation flow with event idempotency.
- An abnormal trigger runs eleven real tools and transactionally persists the anomaly, case,
  evidence, competing hypotheses, history, case events, and a versioned 18-question report.
- Questions 1–5, 10–12, and 17 are populated from evidence when available. Question 9 is explicitly
  unknown until energy balance exists. Question 15 is pending verification. Other unavailable
  Day 5/6 answers remain explicit and explain the limitation.
- `GET /api/cases/{case_id}` exposes report, evidence, hypotheses, and confidence history.
- `GET /api/cases/{case_id}/trace` exposes ordered successful and failed tool calls.
- `GET /api/meters/{meter_id}/precedents` returns exact-meter cases separately and before similar
  closed cases.

## Day 3-J check

```bash
uv run pytest tests/test_investigation_workflow.py::test_replay_creates_one_inspectable_case_and_is_idempotent
```

The test creates one abnormal event, executes eleven tools, persists one case, verifies all eighteen
answer statuses, and repeats the same event to prove that no duplicate case is created.

## Day 4-J checks

```bash
uv run pytest tests/test_day3_day4_analytics.py
uv run pytest tests/test_investigation_workflow.py
```

The suite covers local, shared, and normal peer examples; topology traversal; exact threshold
behavior; evidence-driven leading hypotheses; confidence increases/decreases; precedent ordering;
failed tool traces; API case detail; and API execution trace.

## Clean PostgreSQL gate

```bash
docker compose down -v
docker compose up --build -d db api
docker compose exec api python -m app.seed
docker compose exec api alembic current
```

After importing the Developer A fixture, trigger a timestamp with at least three prior weekly
samples for its weekday/half-hour slot. Repeat the same request body and confirm `duplicate: true`
and the same `case_id`. No evaluation/ground-truth file may be mounted into the API container.
