# Day 3/4 anomaly and peer-analysis specification

MeterDetective is an investigation assistant. A reading anomaly is evidence to investigate, not
proof of fraud, misconduct, or a specific root cause. Calculations are deterministic and evaluation
labels are never available to a tool or agent run.

## Reading quality

`validate_reading_quality` checks missing values, duplicate timestamps, negative/impossible
consumption, zero values, missing half-hour intervals, and consecutive flat readings. A flatline
requires at least three intervals; one repeated reading is not enough. Negative values or a window
with more than 50% missing readings are critical. Other defects lower the quality score and are
recorded as limitations.

```text
penalty = 2*missing + 5*negative + 2*duplicates + 2*missing_intervals + flatline_run
quality = clamp(100 - 100*penalty/max(total_readings, 1), 0, 100)
```

The component counts remain visible, so the score can be recalibrated without hiding its inputs.

## Seasonal baseline and anomaly rules

`calculate_baseline` groups reliable pre-event readings by weekday and half-hour slot. Every usable
profile contains sample count, median, first quartile, third quartile, and IQR. A slot needs at least
three historical samples. Event-period and future readings are excluded to prevent leakage.

For a profile with median `m` and IQR `i`:

```text
tolerance = max(1.5*i, 0.10*abs(m), 0.01)
lower = q1 - tolerance
upper = q3 + tolerance
```

Values below/above those bounds are drops/spikes. Severity is their absolute percentage deviation
from the median, clamped to `0–100`. Missing values score 80, interval gaps start at 55 and increase
by five per missing interval, zero periods score 100, and a confirmed flatline scores 50. These are
development thresholds, not production utility claims.

## Dynamic peers

`select_dynamic_peers` filters on available customer segment and synthetic solar/EV metadata, then
compares normalized recent load shape using Pearson correlation over timestamp intersections. Only
pre-event readings are eligible. A peer requires 12 overlapping intervals and similarity of at
least 0.5; deterministic ordering selects at most five peers.

`compare_with_peers` returns peer count, robust peer median, target deviation, and percentile at the
event timestamp. Missing or duplicate event-time readings produce `unknown` rather than a guess.

## Topology and shared incidents

The topology tool traverses `substation → feeder → transformer → meter` and returns sibling meters.
For every sibling, `detect_shared_incident` compares the event reading with the median of its last
six pre-event readings. A sibling is affected at a drop of at least 40%. With evidence from at least
two siblings, an affected ratio of at least 50% is shared; otherwise it is local. Insufficient
coverage returns `unknown`. Confidence exposes threshold distance and meter coverage.

Question 9 remains explicitly `unknown` during Day 4 because topology association alone does not
prove an upstream mismatch. It becomes answerable after Day 5 tool 11 reconciles transformer input
with downstream energy.

## Required verification cases

- normal readings and normal peers;
- 70% local drop;
- spike, gap, duplicate, impossible value, zero period, and flatline;
- shared transformer drop and exactly-on-threshold shared ratio;
- insufficient history/peers;
- missing metadata and duplicate event-time readings;
- proof that incident and future values are excluded from baseline and peer selection.
