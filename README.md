# MeterDetective AI

Smart-meter anomaly investigation platform. This Day 1-B scaffold freezes the backend,
database, event, tool, agent-state, and 18-question report contracts.

## Start with Docker

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8000/api/health` or the interactive API documentation at
`http://localhost:8000/docs`. The API container applies migrations before it starts.

## Repeatable seed/import

For a quick database smoke test, rebuild the API after pulling these changes and import the
committed synthetic fixture:

```bash
docker compose up --build -d api
docker compose exec api python -m app.seed
docker compose exec api python -m app.seed
```

The second run should report `inserted: 0` for every table. The fixture contains 3 assets,
3 meters, 6 readings, 2 transformer readings, 1 event, 1 anomaly, 1 case, 1 case-meter link,
and 1 tariff.

Developer A's published fixture is now included at `data/fixtures/developer_a`. Prepare it
from the host project directory, then import it into the running API container:

```bash
uv run --python 3.12 --isolated python -m app.prepare_developer_a --source-dir data/fixtures/developer_a
docker compose up --build -d api
docker compose exec api python -m app.seed --manifest data/processed/developer_a/manifest.json
```

This published fixture is the one already imported in the Day 2-B Docker smoke test: 10 assets,
160 meters, 213,710 readings, 8,646 transformer readings, 15 events, 15 cases, 399 case-meter
links, and 3 tariffs. Preparation writes normalized CSVs and a manifest to the ignored
`data/processed/developer_a` directory, which Compose mounts read-only into the API container.
Run the import command again to verify idempotency. It rejects a reused natural key with
different values and rolls back the whole import.

The separate deterministic rebuild starts from Developer A's raw LCL source and recreates
synthetic topology, metadata, readings, transformer totals, tickets, and anomaly scenarios:

```bash
uv run --python 3.12 --isolated python -m app.rebuild_developer_a
uv run --python 3.12 --isolated python -m experiments.developer_a_agent --offline
```

The rebuild's import manifest is at `data/processed/rebuilt_developer_a/import/manifest.json`.
Its scenario labels are held outside the API-mounted directory, at
`data/evaluation/rebuilt_developer_a/ground_truth.csv`. The published fixture and rebuild are
intentionally separate datasets; rebuilding does not change the already-imported records.
See [docs/developer_a_integration.md](docs/developer_a_integration.md) for provenance and caveats.

Developer A's timestamps have no timezone, so preparation interprets them as UTC. Its tariff
has three consumption brackets; the current flat-rate tariff table stores one row per bracket,
with the bracket range in the name. The fixed charge remains in the source JSON for later
tariff-calculation work.

The manifest format is `{"format_version": 1, "tables": {"meters": [...], "readings":
{"file": "readings.csv"}}}`. Table rows can be inline JSON or a relative CSV/JSON file.
Asset rows must be ordered parent-first. Supported tables are listed in `app/seed.py`.

## Local development

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run uvicorn app.main:app --reload
```

For a local API process, set `DATABASE_URL` to use `localhost` instead of the Compose
service name `db`. Copy `.env.example` to `.env` and change only that host.

See [docs/architecture.md](docs/architecture.md) and [docs/contracts.md](docs/contracts.md)
for the frozen Day 1 contracts.
