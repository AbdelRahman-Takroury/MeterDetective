# Developer A integration checkpoint

Developer A's Day 1/Day 2 source is `origin/main` at
`b3739db8636772211cc801fc2066298c1c039491`. This integration copies its published
data and ports its preparation, scenario, and three agent-tool workflows into the Day 2-B
branch. It does not merge the unrelated Git histories or replace the running API.

## Data paths

| Path | Purpose |
| --- | --- |
| `data/source/developer_a/LCL-June2015v2_98.csv` | Raw LCL source sample |
| `data/fixtures/developer_a/` | Developer A's published CSV/JSON fixtures; unchanged input to the existing Docker import |
| `data/processed/developer_a/` | Ignored canonical output of `app.prepare_developer_a`; API container mounts this read-only |
| `data/processed/rebuilt_developer_a/` | Ignored deterministic rebuild; includes its own canonical `import/` manifest |
| `data/evaluation/developer_a/ground_truth.csv` | Published evaluation labels |
| `data/evaluation/rebuilt_developer_a/ground_truth.csv` | Rebuilt scenario labels |

The API Docker image excludes source, fixture, and evaluation directories. Compose mounts
only `data/processed`, so agent tools and the API cannot read evaluation labels by default.
Do not put ground truth into `data/processed` or expose it through an endpoint.

## Published fixture import

Run `python -m app.prepare_developer_a --source-dir data/fixtures/developer_a` on the host,
then `docker compose exec api python -m app.seed --manifest
data/processed/developer_a/manifest.json`. The output matches the previously prepared
manifest and reading files byte-for-byte. The first import in the Docker smoke test inserted
10 assets, 160 meters, 213,710 readings, 8,646 transformer readings, 15 events, 15 cases,
399 case-meter links, and 3 tariffs. The second inserted zero of each. Database natural-key
constraints and the importer reject conflicting duplicates; identical rows are skipped.

## Deterministic rebuild

`python -m app.rebuild_developer_a` takes the raw LCL file and uses a fixed seed to assign
meters to transformers, add four noisy copies per active source meter, synthesize metadata
and tickets, and inject a local 70% drop on day 15 and a TX_3 shared 40% drop on day 18.
It writes SHA-256 hashes to `profile.json` and generates a canonical import under `import/`.
Two full rebuilds produced identical file and truth hashes. The scenario dates are relative
to the actual reading window; the original prototype's fixed 2013 date was outside that
window. The published fixture remains unchanged, so it retains Developer A's original
scenario behavior and historical counts.

`experiments/developer_a_agent.py` ports the three read-only prototype tools (transformer
readings, tariff, and historical alerts) into an isolated experiment. The `--offline` mode
needs no external service. A live model run is optional: install `groq`, set `GROQ_API_KEY`
in the environment, and run without `--offline`. The API never imports this experiment.
The source prototype on `origin/main` contained a credential-like literal. No credential
was copied into this branch; revoke/rotate the exposed credential outside this repository.

## Data assumptions and current boundaries

- Source timestamps lack timezone information. The canonical importer treats them as UTC.
- The JOD tariff's three brackets become three flat-rate records; the fixed charge and
  progressive calculation are not yet implemented in the Day 2-B tariff model.
- The evaluation labels are not evidence available to an investigation agent.
- This checkpoint preserves the original published data and adds a reproducible derivative;
  it does not make claims about the statistical realism of the synthetic anomalies.
