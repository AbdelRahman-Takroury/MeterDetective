# Day 6 post-action verification policy

Policy version: `day6-v1`

Verification uses only readings before the anomaly to build the weekday/half-hour baseline.
Readings in the requested post-action window are then compared with that baseline and with
meters on the same transformer.

## Default thresholds

- Minimum valid observations: 4 (two hours for 30-minute readings).
- Baseline recovery: at least 75% of valid observations inside the robust baseline band.
- Peer recovery: at least 75% of comparable observations within 20% of the peer median.
- Baseline band: Q1–Q3 extended by 1.5 IQR, with a minimum tolerance of 20% of the median.

The values are configurable through `Settings`; changing them requires a new policy version.

## Outcomes

| Outcome | Rule | Case/report effect |
|---|---|---|
| `recovered` | Baseline and available peer checks pass with reliable data | Resolve case; answer Question 15; remove from active queue |
| `persistent` | Enough reliable data exists but recovery checks fail | Reopen case; answer Question 15; require replanning |
| `insufficient_observations` | Fewer than four valid baseline-comparable readings | Monitor; keep Question 15 pending |
| `ambiguous` | Baseline and peer signals disagree | Monitor; mark Question 15 unknown with limitations |

All results persist the observation window, expected/observed energy, baseline fraction, peer
fraction, median deviation, confidence, and policy version as evidence. A completed and properly
approved simulated action is required before verification.
