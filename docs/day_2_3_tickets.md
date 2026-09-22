# Day 2–3 tickets

These are repository-local ticket drafts pending creation in the team's chosen board.

## A1 — Data ingestion and scenario fixtures (Owner: Developer A, Day 2)

Acceptance: deterministic import populates meters/readings/topology; both scenario IDs and ground
truth are reproducible; raw data is not committed; data dictionary and profiling output exist.

## B1 — Persistence repositories and CRUD API (Owner: Developer B, Day 2)

Acceptance: migrations apply cleanly; meter, reading, anomaly, and case repositories persist and
query data; minimum list/detail endpoints have validation, error handling, and integration tests.

## A2 — Quality, baseline, and anomaly tools (Owner: Developer A, Day 3)

Acceptance: tools 3–5 use the common execution contract, log evidence and latency, pass unit tests,
and detect Scenario 1's 70% drop without flagging unaffected neighbors.

## B2 — First investigation vertical slice (Owner: Developer B, Day 3)

Acceptance: one injected anomaly triggers real tool calls, persists run/tool logs and one case,
and creates a schema-valid partial report with all 18 statuses; trace and report endpoints expose it.

## AB1 — Clean-run integration gate (Owners: A + B, end of Day 3)

Acceptance: from a clean checkout, one command starts the stack, seeds data, replays Scenario 1's
trigger, and yields one inspectable persisted case; CI and documented reset steps pass.

