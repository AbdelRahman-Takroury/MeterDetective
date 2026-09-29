# Day 1 Developer B — tasks 5 through 10

Investigation plans are now persisted independently from the 18-question report.
Each plan has a case-local version, status, goal, readable steps, evidence IDs,
creation time, source event/run/report, and policy version. Old goals, steps, and
evidence snapshots remain unchanged when a plan is invalidated. Only its lifecycle
status and invalidation timestamp change.

## Database upgrade

Migration `20260928_0005` creates `investigation_plans`. Docker startup applies the
migration automatically after rebuilding the API:

```powershell
docker compose up --build -d api
```

For a local API configured to connect to PostgreSQL, stop the API, run
`.\.venv\Scripts\python.exe -m alembic upgrade head`, then restart it.
Existing cases are preserved. Plans are not fabricated for historical reports;
an existing case receives its first plan when its next investigation completes.

The schema enforces positive case-local versions, unique `(case_id, version)`,
valid statuses/scopes, and at most one active plan per case. Case-row locking
serializes plan version assignment on PostgreSQL. Scenario resets remove owned
plans before their referenced reports, runs, or events.

## Invalidation policy

Policy `evidence-scope-v1` derives scope from stored reading-quality and
shared-incident evidence, never from a scenario identifier or expected result.

| Observation | Plan behavior |
|---|---|
| First investigation | Record version 1 |
| Same assessed scope | Retain current plan version; new report/evidence still persist |
| Missing or unreliable scope evidence | Retain existing plan; no false contradiction |
| Reliable local-to-shared or shared-to-local change | Invalidate earlier plan and create next version |
| Previously unknown scope becomes known | Supersede the evidence-gathering plan and record `plan_refined` |
| Duplicate incoming event | Return existing result; no duplicate versions or events |

An invalidation produces one `plan_invalidated` case event with old/new plan IDs
and versions, source event/run/report IDs, evidence links, a policy version, and
plain-language message and reason. Example:

> New evidence changed the investigation plan. Several related meters now show
> the same drop, suggesting a shared network issue.

Shared-meter evidence changes the investigation's scope; it does not establish a
definitive transformer root cause. The shared plan explicitly includes comparing
transformer input with meter totals. Outcome verification and full plan execution
tracking remain separate workflows. `active` means current plan version, not a
claim that the case is still unresolved.

## API and inspection

`GET /api/cases/{case_id}` now includes `plans`, ordered by version. Each item has
`goal`, `steps`, `status`, evidence links, and provenance. Case events contain the
invalidation explanation. Successful run state contains `plan_id` and `plan_version`.
These fields support the planned plain-language plan-history UI; this increment
does not add the later Live Operations controls or expose additional raw data in
the operator interface.

Reset Scenario 2, replay `initial`, inspect the case, then replay `shared` and
inspect it again. Expect plan 1 local/invalidated and plan 2 shared/active, with
exactly one invalidation despite multiple affected-meter events. Repeating the
stage must not change plan counts or history.

## Recommendation and report revisions

A recommendation records the plan and report that produced it. When reliable
evidence changes the plan, the service creates the revised report and replacement
recommendation first, then marks any earlier unexecuted recommendation and action
as `superseded`. The earlier row, approval decision, rationale, and timestamps stay
available. `superseded_by_id` links the recommendations, and a
`recommendation_superseded` case event explains the transition.

Evidence that does not change the active plan refreshes the report but reuses the
current recommendation and action. This prevents repeated readings from producing
extra approval requests. Questions 13 and 14 remain present in the latest report.

Successful runs persist their public result status, anomaly count, and workflow
identifiers. Redelivery of the same canonical meter and UTC timestamp returns that
original result and trace without creating another event, run, report, plan,
recommendation, action, or case-history item.

## Developer A integration contracts

- Scenario 2 Stage A is September 21, 2026 at 12:00 UTC; Stage B is 12:30 UTC.
- Stage B updates the same case.
- The relevant case is identified before precedent lookup. Its ID is excluded
  from exact and similar searches before limits are applied. A case returned as
  an exact precedent is not counted again as a similar precedent.
- Revenue remains the existing deterministic per-event calculation. Nothing in
  plan versioning adds or sums revenue. Earlier estimates remain in report history.
- The new backend modules do not create or overwrite Developer A's input fixture.
  The current synthetic service scaffold remains until that fixture is supplied.

## Validation

Tests cover version preservation, evidence-linked invalidation, revised reports,
recommendation lineage, preservation of an earlier approval, disabled superseded
actions, exact duplicate results, missing/unreliable evidence, self-precedent
exclusion, preserved historical precedents, scenario reset isolation, and rollback
after a plan update fails. Migration tests generate upgrade/downgrade SQL and
compare it with the ORM schema. Integration tests use SQLite with foreign keys
enabled; a live PostgreSQL upgrade/concurrency check remains part of the
demo-environment gate.
