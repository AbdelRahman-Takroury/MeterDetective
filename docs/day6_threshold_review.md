# Day 6 Anomaly Threshold Review

## Objective

This development-only sensitivity review satisfies the Day 6-A requirement to review local anomaly thresholds using development samples and document the rationale for the selected thresholds. It does not change the production formula.

## Current Production Policy

The production detector uses:

    tolerance = max(1.5 * IQR, 0.10 * abs(median), 0.01)

    drop  when reading < Q1 - tolerance
    spike when reading > Q3 + tolerance

The comparisons are strict: readings exactly at either bound are not anomalous. The reviewed current values are an IQR multiplier of 1.50, a median-relative multiplier of 0.10, and a floor of 0.01 kWh.

## Data Separation

The review uses only deterministic, synthetic development samples defined in tests/test_day6_threshold_review.py. Existing examples in tests/test_day3_day4_analytics.py are treated as extreme smoke examples, not as sufficient calibration data.

Scenario 1 remains a regression fixture and was not used to select thresholds. data/evaluation/developer_a/ground_truth.csv is held-out evaluation data and was not used. Rebuilt evaluation ground-truth labels were also not used.

## Development Profiles

For each profile, samples include the median as a clear normal point, the exact lower and upper bounds as normal points, and the immediately adjacent floating-point values outside the bounds as labeled drop and spike points.

| Profile | Median | Q1 | Q3 | IQR | IQR term | Median term | Floor | Current bounds | Dominant term |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| IQR-dominant | 11.0 | 10.0 | 12.0 | 2.0 | 3.0 | 1.10 | 0.01 | [7.0, 15.0] | 1.5 × IQR |
| Median-relative-dominant | 10.0 | 9.8 | 10.2 | 0.4 | 0.60 | 1.00 | 0.01 | [8.8, 11.2] | 10% of median |
| Absolute-floor-dominant | 0.04 | 0.038 | 0.042 | 0.004 | 0.006 | 0.004 | 0.01 | [0.028, 0.052] | 0.01 kWh floor |

The test asserts the strict dominance inequalities for all three profiles; dominance is measured for the current 1.50 / 0.10 / 0.01 setting.

## Candidate Grid

The experiment evaluates the requested 3 × 3 grid, keeping the absolute floor fixed at 0.01 kWh. Each candidate sees 9 normal-labeled samples (three per profile, including both exact boundaries), 3 labeled drops, and 3 labeled spikes.

| IQR multiplier | Median multiplier | Normal FP | Drops detected | Spikes detected |
|---:|---:|---:|---:|---:|
| 1.25 | 0.08 | 4 | 3/3 | 3/3 |
| 1.25 | 0.10 | 2 | 3/3 | 3/3 |
| 1.25 | 0.12 | 2 | 2/3 | 2/3 |
| 1.50 | 0.08 | 2 | 3/3 | 3/3 |
| **1.50 CURRENT** | **0.10 CURRENT** | **0** | **3/3** | **3/3** |
| 1.50 | 0.12 | 0 | 2/3 | 2/3 |
| 1.75 | 0.08 | 2 | 2/3 | 2/3 |
| 1.75 | 0.10 | 0 | 2/3 | 2/3 |
| 1.75 | 0.12 | 0 | 1/3 | 1/3 |

## Decision

Retain the current production policy: 1.50 × IQR, 0.10 × absolute median, and a 0.01 kWh floor. On this deliberately small sensitivity set, the current setting preserves every expected development classification and has zero false positives among the normal-labeled samples. No alternative has evidence of a better trade-off on these samples: narrower candidates add normal false positives, while wider candidates miss labeled outside-bound events.

The rationale is:

- 1.5 × IQR provides robust tolerance for naturally variable profiles.
- 10% of the median prevents the envelope becoming unrealistically narrow when IQR is very small or collapses to zero.
- 0.01 kWh provides a minimum absolute tolerance near zero usage.
- The reviewed development samples do not provide sufficient evidence to justify modifying the existing Day 1–5 formula.
- This review is sensitivity evidence, not proof of globally optimal thresholds.

The labels are intentionally defined around the current envelope to exercise exact-boundary behavior and compare sensitivity. Therefore, these results support retaining the current development policy; they are not independent statistical calibration.

## Limitations

The sample is small and synthetic, and is not representative of all meter populations. The results support reviewing and retaining the existing development thresholds but do not establish globally optimal calibration. A larger legitimate development set would be needed to assess behavior across real profile distributions. No statistical significance or benchmark claim is made.

## Known Documentation Inconsistency

The pre-existing example in docs/examples/day3_day4_tool_outputs.json reports a lower bound inconsistent with the current formula. Its values are median = 10, Q1 = 9.9, Q3 = 10.1, and IQR = 0.2. The terms are 1.5 × 0.2 = 0.3, 0.10 × 10 = 1.0, and the 0.01 floor, so tolerance is 1.0 and the lower bound is 9.9 − 1.0 = 8.9. The old example reports 9.6. This is a pre-existing documentation inconsistency; production logic was not changed to match the stale example.
