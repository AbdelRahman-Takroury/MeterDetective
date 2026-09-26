# Day 5 failure-behavior evidence

External and optional evidence failures degrade the report without aborting the
investigation:

| Failure | Persisted behavior | Test evidence |
| --- | --- | --- |
| Weather timeout | One bounded retry, then question 6 is `unknown` with confidence 0 and a limitation | `test_weather_retries_once_returns_controlled_failure_and_caches_success`; integrated outage assertion in `test_replay_creates_one_inspectable_case_and_is_idempotent` |
| No relevant RAG result | Question 12 remains evidence-backed by deterministic investigation data and records that no relevant stored document was found | `test_knowledge_ingestion_citations_and_no_result`; integrated assertion in `test_replay_creates_one_inspectable_case_and_is_idempotent` |
| Invalid LLM JSON | One repair request is attempted; a second invalid result raises the controlled provider error | `test_llm_invalid_json_gets_exactly_one_repair_and_numerical_extras_are_rejected` |
| Missing or ambiguous tariff | Question 16 is `unknown`; no `financial_impacts` row is created | `test_replay_creates_one_inspectable_case_and_is_idempotent` |
| Missing revenue during triage | Revenue contribution is explicitly zero and the limitation is attached to question 18 | `test_replay_creates_one_inspectable_case_and_is_idempotent` |

Weather and technical retrieval remain contextual inputs. They do not overwrite meter
readings, baselines, anomaly magnitude, energy balance, revenue, or triage calculations.
