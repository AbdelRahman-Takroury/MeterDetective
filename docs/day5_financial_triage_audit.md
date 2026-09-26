# Day 5 financial and triage calculation audit

Revenue at Risk is produced only by `estimate_revenue_at_risk`. Its persisted record
contains the low/base/high missing-energy and JOD ranges, tariff ID and version, source,
calculation window, assumptions, method version, and confidence. The LLM adapter cannot
populate these numerical fields.

Triage is produced only by `calculate_triage_priority`. Its persisted assessment contains
the score, P1-P4 band, active rank, active count, percentile, normalized factors,
contributions, weights, policy version, and queue semantics. The calculation uses strict
higher/lower comparisons, so equal scores retain equal rank and are excluded from the
lower-count percentile numerator.

When a new active case changes the queue snapshot, each affected prior case receives:

1. a new investigation-report version with refreshed question 18;
2. a new triage-assessment row linked to that report version;
3. a `triage_recalculated` case event identifying the triggering case;
4. a copied financial-impact row linked to the new report version, preserving the exact
   tariff, assumptions, values, and confidence used previously.

Reproducibility and history are covered by:

- `tests/test_day5_advanced_analytics.py::test_revenue_at_risk_calculation`
- `tests/test_day5_advanced_analytics.py::test_triage_priority_calculation`
- `tests/test_day5_advanced_analytics.py::test_triage_rank_changes_with_active_queue`
- `tests/test_day5_advanced_analytics.py::test_triage_ties_have_same_rank_and_are_not_counted_lower`
- `tests/test_investigation_workflow.py::test_active_queue_change_versions_triage_and_financial_history`

Verification command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-day5j
```

Result on 2026-09-26: 108 tests passed.
