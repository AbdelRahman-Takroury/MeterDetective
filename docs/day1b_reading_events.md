# Day 1 Developer B — tasks 1–4

Implemented: Scenario 2 reset/replay entry points, persisted reading-received events,
automatic anomaly routing, and resumption of a relevant active case.

## Try Scenario 2

Start the API using the README instructions, then use the API documentation at
`http://localhost:8000/docs` or PowerShell:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/replay/scenario-2/reset

Invoke-RestMethod -Method Post http://localhost:8000/api/replay/scenario-2/replay `
  -ContentType 'application/json' -Body '{"stage":"normal"}'

Invoke-RestMethod -Method Post http://localhost:8000/api/replay/scenario-2/replay `
  -ContentType 'application/json' -Body '{"stage":"initial"}'

Invoke-RestMethod -Method Post http://localhost:8000/api/replay/scenario-2/replay `
  -ContentType 'application/json' -Body '{"stage":"shared"}'
```

These are developer verification instructions. The operator-facing replay controls
and plain-language activity presentation are scheduled in the later UI tasks.

| Operation | Expected outcome |
|---|---|
| Reset | Loads 28 days of synthetic history for SC2-M1–SC2-M3; no investigation yet |
| Normal | Three reading events, three normal stops, no case |
| Initial | Target drops to 3 kWh; peers remain normal; one case is created |
| Shared | Thirty minutes later all three meters drop; transformer input evidence arrives; the same case is updated |
| Repeat a stage | Existing event/run returned with `duplicate: true`; no extra readings, runs, or reports |
| Reset again | Removes only the SC2 fixture and its investigation records, preserving Scenario 1 and unrelated imports |

Replay returns one result per meter event. In the initial response the target has
`case_created`; the other two results are normal. In the shared response all three
results have `case_updated` and the same case ID. Each abnormal event has its own
auditable run and report refresh, so a batch can create multiple report versions.

Open the returned case using `http://localhost:8000/#/cases/CASE_ID`, or inspect
`GET /api/cases/CASE_ID` and `GET /api/cases/CASE_ID/trace`. Prior report versions,
evidence, hypotheses, and run history remain stored. The case history contains
`investigation_resumed` events identifying the incoming event and resulting report.

## Receive ordinary readings

`POST /api/replay/readings` accepts a batch of 1–100 readings for existing meters:

```json
{
  "readings": [
    {
      "meter_id": "SC2-M1",
      "timestamp": "2026-09-21T13:00:00Z",
      "kwh": 3.0,
      "quality_flag": "valid",
      "source": "meter-upload"
    }
  ]
}
```

Timestamps must include a timezone. Energy must be finite and nonnegative. Unknown
meters, conflicting values at an existing meter/timestamp, and invalid batches are
rejected without partial ingestion. UTC canonical event keys deduplicate repeated
deliveries. Source and quality are persisted on the immutable reading; each event
references that reading through its meter and UTC timestamp.

All batch readings are stored before detection starts so comparison tools see a
coherent peer window. Events are dispatched synchronously, in timestamp/meter order,
inside the request; this is not a background scheduler or streaming server. No user
prompt or case-creation request is required after readings are submitted.

## Automatic decisions and case matching

The existing controller checks meter context, reading quality, baseline, and anomaly
thresholds. Normal readings stop after detection. Abnormal readings continue through
the existing investigation tools.

Case matching uses evidence, not the scenario name:

1. Look for an active investigation with the same anomaly type and a successful
   run observing an anomaly within the preceding two hours of the reading time.
2. Prefer one unambiguous case already linked to the incoming meter.
3. Otherwise require the same transformer and a linked meter independently classified
   as affected by the current shared-incident analysis.
4. If no unambiguous match exists, create a new case. Closed/resolved cases and
   unrelated network incidents are not automatically merged.

Eligible statuses are investigating, awaiting approval, action ready, monitoring,
and reopened. Cases waiting for post-action verification remain with the existing
verification workflow. The two-hour incident window is an explicit demo policy;
it is not a claim of a production-calibrated correlation model.

Resumption appends evidence and report versions, updates hypothesis confidence
history, links affected meters, and reassesses priority. The existing proposal tool
issues a fresh approval request. Earlier unexecuted recommendations/actions are
marked superseded so an outdated action cannot execute. Completed actions and
approval audit records are retained. Plan versions and plan-invalidation events
are now implemented in tasks 5–6; see [Plan versions](day1b_plan_versions.md).
Stage A is September 21, 2026 at 12:00 UTC; Stage B is at 12:30 UTC.

## Failure and transaction behavior

Invalid ingestion rolls back the request. A controlled investigation failure keeps
the accepted readings and marks the event/run failed, while rolling back partial
case mutations for that event. Tool traces are retained, including failures; outputs
from rolled-back steps are audit observations and are labeled by the run's
`domain_changes_rolled_back` flag. Later events in the batch can still be processed.
The response includes `status: failed`, which callers must check even on HTTP 200.
Redelivery returns the existing failed outcome; automatic retry of failed events
is not part of this increment.

On PostgreSQL, network and meter row locks serialize concurrent arrivals to the
same network. SQLite integration tests verify persistence, foreign keys, routing,
resumption, safety, and reset boundaries; they do not validate PostgreSQL concurrency.

Scenario 2 uses synthetic readings, topology, tariff, and weather. The transformer
measurement is inserted only during the shared stage. Ground-truth labels are not
provided to the controller.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_scenario_two.py tests/test_scenario_one.py
```

Coverage includes normal stops, automatic case creation, shared-case resumption,
retained history, duplicate delivery, conflicting batches, invalid timestamps,
stale/closed/unrelated cases, failure rollback and retained receipts, and reset
isolation with foreign-key enforcement enabled.
