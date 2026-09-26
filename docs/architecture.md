# Day 1 architecture

MeterDetective is a modular monolith: one FastAPI service and one PostgreSQL/pgvector database.
The replay process emits events; deterministic validation and analytics detect abnormalities; an
investigation state machine chooses typed tools; persisted evidence feeds a versioned report and
approval-gated workflow. Numerical results come from Python tools, never from an LLM.

```text
reading replay -> validation/anomaly -> investigation run -> typed tools
       |                                      |
       v                                      v
  PostgreSQL <---- evidence/case/report/workflow state ---- approval + verification
```

## Runtime boundaries

- `app/api`: HTTP transport; routes remain thin.
- `app/contracts`: strict, serializable interfaces shared by tools, state, and API layers.
- `app/db`: SQLAlchemy persistence models and session lifecycle.
- `alembic`: versioned database changes. PostgreSQL's `vector` extension is enabled initially.
- `frontend`: reserved for the dependency-free HTML/CSS/JavaScript dashboard.
- `tests`: unit, integration, and later scenario tests.

## Persistence relationships

A transformer asset owns meters and transformer readings. Events produce anomalies. Cases link to
one or more meters, evidence, hypotheses, recommendations, actions, chronological events, and
versioned reports. Agent runs own tool execution records. Each report owns reproducible financial
and triage assessments. Documents own vectorized chunks for retrieval.

Indexes cover meter/time reading lookups, transformer/time lookups, foreign keys, case status,
priority, and operational status fields. Exact-meter history is retrieved before similar cases.

## Failure and safety rules

- Database failure makes `/api/health` return HTTP 503 without leaking driver details.
- Tool failures are persisted as structured errors; the agent may retry, select another source,
  or report missing evidence.
- Inspection/work-order actions remain simulated and require a persisted approval decision.
- Reports cannot omit questions, silently invent evidence, or lose tariff/policy versions.

