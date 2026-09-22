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

