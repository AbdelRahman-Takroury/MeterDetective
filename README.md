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

Developer A's data currently lives on `origin/main`, not this branch. The command below pins
the source commit so the documented counts remain reproducible. Run it from the host project
directory without switching branches:

```bash
uv run --python 3.12 --isolated python -m app.prepare_developer_a --ref b3739db8636772211cc801fc2066298c1c039491
docker compose up --build -d api
docker compose exec api python -m app.seed --manifest data/processed/developer_a/manifest.json
```

The preparation command also accepts `--source-dir PATH` when the source CSV/JSON files are
already checked out locally. It writes normalized CSVs and a manifest to the ignored
`data/processed/developer_a` directory, which Compose mounts read-only into the API container.
Run the import command again to verify idempotency. It rejects a reused natural key with
different values and rolls back the whole import.

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
