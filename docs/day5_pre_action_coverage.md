# Day 5 pre-action report coverage

Verified on 2026-09-26 against the deterministic replay workflow.

| Questions | Source | Required outcome |
| --- | --- | --- |
| 1-3 | anomaly, severity, reading quality | Answered from deterministic meter calculations |
| 4-5 | peer comparison and shared-incident analysis | Answered when qualified peers exist; otherwise explicitly `unknown` |
| 6 | weather alignment | Answered as contextual evidence, or explicitly `unknown` after a controlled outage |
| 7-8 | customer behavior, EV, and solar context | Answered from configured metadata/signatures, or explicitly `unknown` |
| 9 | transformer energy balance | Answered when transformer and downstream intervals exist; otherwise explicitly `unknown` |
| 10-12 | hypothesis, confidence, evidence, and technical citations | Answered from stored evidence; no-result retrieval is recorded as a limitation |
| 13-14 | recommendation and approval | Explicitly `unknown` before an action decision |
| 15 | outcome verification | Explicitly `pending_verification` before post-action readings exist |
| 16 | Revenue at Risk | Answered only with forecast, reliable observations, and one applicable tariff; otherwise explicitly `unknown` |
| 17 | meter precedents | Answered with case IDs or an explicit no-precedent result |
| 18 | triage | Answered from the deterministic scoring policy and active queue snapshot |

Every persisted question contains the explicit report-contract fields: status, answer,
structured values, confidence, supporting/contradicting evidence, tools, data sources,
limitations, and freshness. The report validator rejects missing fields, unanswered items
without limitations, answered items without evidence or freshness, and numerical values
attributed only to an LLM. A report cannot enter `complete` while verification remains
pending.

Automated evidence:

- `tests/test_investigation_workflow.py::test_replay_creates_one_inspectable_case_and_is_idempotent`
- `tests/test_day5b_external_evidence.py::test_report_validator_enforces_completeness_and_numerical_ownership`
- `tests/test_day5b_external_evidence.py::test_complete_report_requires_answered_verification`
