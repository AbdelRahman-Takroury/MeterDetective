# Day 6 Scenario 1 acceptance

Scenario 1 is an isolated, deterministic demonstration slice. Its meter IDs begin with `SC1-`,
its readings use source `scenario-1-local-drop`, and its topology uses fixed UUIDs. Reset removes
only records owned by this slice, recreates 28 days of half-hour history, injects a 70% local drop,
adds a synthetic residential tariff, and runs the real investigation service. Historical imported
cases are not treated as Scenario 1 records.

## Visible workflow

1. Select **Reset & run Scenario 1** in the case queue.
2. Inspect the persisted 18-question report, evidence, hypotheses, citations, financial impact,
   precedents, triage, recommendation, and agent trace.
3. Reject once to demonstrate that execution stays blocked, then reset again.
4. Approve the technician inspection and select **Apply simulated repair**.
5. The action inserts four deterministic half-hour recovery readings for the target and peers.
6. Select **Verify readings** with the automatically populated UTC window.
7. Verification compares the new observations with the historical baseline and peers, versions
   the report, answers Question 15 as `recovered`, and resolves the case.

`tests/test_scenario_one.py` executes three clean resets and covers pre-approval blocking,
rejection, repair idempotency boundaries, post-action readings, report coverage, resolution, and
preservation of an unrelated imported case.
