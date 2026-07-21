# Stratix backend

Phase 0 provides the FastAPI foundation for Stratix: environment-based settings,
structured JSON logs, request correlation IDs, stable API errors, and health checks.
It intentionally has no database, crawler, authentication, agent, or business API yet.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

From this directory, create the environment and install locked dependencies:

```bash
uv sync --dev
```

Copy `.env.example` to `.env` and adjust local values if needed. Never commit `.env`.

## Run

Start the development server:

```bash
uv run fastapi dev app/main.py
```

The health endpoints are available at:

- `GET http://127.0.0.1:8000/health/live`
- `GET http://127.0.0.1:8000/health/ready`

OpenAPI documentation is at `http://127.0.0.1:8000/docs`.

## Validate

```bash
uv run ruff format .
uv run ruff check .
uv run mypy app
uv run pytest
```
