# Stratix Backend

Stratix is a strategic intelligence platform for startups and product teams. It is
designed to monitor public competitor activity, detect objective changes, and turn
verified signals into evidence-backed strategic insights.

## Phase 1 status

Phase 1 preserves the Phase 0 backend foundation and adds the first product vertical
slice:

- FastAPI application factory and lifespan handling
- Environment-based, validated application settings
- Structured JSON logging
- Request correlation IDs through the `X-Request-ID` header
- Stable API error envelopes
- Liveness and readiness health endpoints
- Official asynchronous PyMongo client lifecycle and MongoDB-backed readiness
- Idempotent organization-scoped company onboarding
- Company profile retrieval and updates
- Automatic creation of the primary company website source
- Persisted initialization jobs and onboarding progress APIs
- Replaceable deterministic initialization runner and background-execution boundary
- Offline tests using in-memory persistence and background-execution fakes
- Ruff, mypy, and pytest development tooling

The onboarding request returns `202 Accepted` as soon as its company profile, website
source, initialization job, and idempotency result are persisted. No crawling,
competitor discovery, or AI reasoning runs during the request.

## Technology stack

- Python 3.12
- FastAPI
- Pydantic and pydantic-settings
- PyMongo `AsyncMongoClient` (no ODM)
- structlog
- uv for dependency and environment management
- Ruff for formatting and linting
- mypy for static type checking
- pytest and HTTPX for testing

## Project structure

```text
backend/
|-- app/
|   |-- api/
|   |   |-- v1/             # Company onboarding and status routes
|   |   |-- dependencies.py # Auth and service dependencies
|   |   |-- errors.py       # Stable API error responses
|   |   `-- health.py       # MongoDB-aware health routes
|   |-- core/
|   |   |-- auth.py         # Trusted authenticated context
|   |   |-- config.py       # Environment-backed settings
|   |   |-- logging.py      # Structured logging configuration
|   |   `-- request_id.py   # Request ID utilities
|   |-- domain/             # Company, source, job, and API models
|   |-- repositories/       # Persistence protocols and PyMongo implementations
|   |-- services/           # Onboarding and deterministic initialization
|   |-- main.py             # FastAPI application factory and entry point
|   `-- middleware.py       # Request context middleware
|-- tests/                  # Phase 0 automated tests
|-- .env.example            # Safe local configuration template
|-- AGENTS.md               # Backend contributor instructions
|-- PRD.md                  # Backend product requirements
|-- pyproject.toml          # Project metadata, dependencies, and tool settings
|-- uv.lock                 # Locked dependency versions
`-- README.md
```

## Prerequisites

- Python 3.12
- uv

## Install uv and set up the project

Install uv on macOS or Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install uv on Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

From the repository root, enter the backend directory and install the locked project
and development dependencies:

```bash
cd backend
uv sync --dev
```

## Environment setup

Create a local `.env` from the committed example:

macOS or Linux:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Review the copied values and adjust them for your local environment. The application
loads `.env` automatically.

Phase 1 MongoDB and development-authentication settings are:

```dotenv
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=stratix
MONGODB_CONNECT_TIMEOUT_MS=5000
MONGODB_SERVER_SELECTION_TIMEOUT_MS=5000
AUTH_DISABLED=true
DEV_USER_ID=user_development
DEV_ORGANIZATION_ID=org_development
```

For MongoDB Atlas, replace `MONGODB_URI` only in your uncommitted `.env`. Atlas URIs
often contain database usernames and passwords and must be treated as credentials.

> [!WARNING]
> Never commit `.env`, credentials, API keys, tokens, or other secrets to version
> control.

## Development authentication

Clerk verification is intentionally deferred. The development identity is active
only when both of these settings are present:

```dotenv
APP_ENV=development
AUTH_DISABLED=true
```

In that mode, `DEV_USER_ID` and `DEV_ORGANIZATION_ID` supply the trusted request
context. User and organization IDs are never accepted from request bodies, and every
tenant-owned persistence query includes the trusted organization ID. Configuration
validation rejects `AUTH_DISABLED=true` when `APP_ENV=production`.

## Run the development server

Run this command from `backend/`:

```bash
uv run fastapi dev app/main.py
```

FastAPI serves the local API at `http://127.0.0.1:8000` and the interactive OpenAPI
documentation at `http://127.0.0.1:8000/docs`.

## Health endpoints

- `GET /health/live` confirms that the API process can serve requests.
- `GET /health/ready` confirms that application startup is complete and currently
  required dependencies are ready, including a successful MongoDB ping.

## Company onboarding API

All Phase 1 product endpoints are scoped to the authenticated organization:

- `POST /api/v1/onboarding/company` submits the company profile. It requires an
  `Idempotency-Key` header and returns the persisted company and queued job with
  `202 Accepted`.
- `GET /api/v1/company` returns the organization's company profile.
- `PATCH /api/v1/company` updates editable company fields. Identity, ownership,
  timestamps, and onboarding status cannot be changed by clients.
- `GET /api/v1/onboarding/status` returns the company state and latest job summary.
- `GET /api/v1/initialization-jobs/{job_id}` returns an organization-scoped job.

Repeating an onboarding request with the same idempotency key and payload returns the
original persisted result without creating duplicates. A different key is rejected
once the organization has a primary company profile.

Phase 1 uses explicit compensation when a multi-document onboarding write fails,
which also keeps offline fakes and standalone MongoDB deployments supported without
requiring transactions.

## Initialization jobs

The deterministic runner validates the persisted company, confirms and activates its
primary website source, prepares minimal workspace metadata, updates progress, and
marks the company ready. It does not crawl, discover competitors, or invoke AI.

Local development uses an application-managed asynchronous task. The job is persisted
before scheduling and the HTTP response does not wait for initialization. This task
mechanism is not durable: a process restart may interrupt a running job. The executor
boundary is designed to be replaced by a durable queue in a later phase.

## Validation

Run the complete development validation suite from `backend/`:

```bash
uv run ruff format .
uv run ruff check .
uv run mypy app
uv run pytest
```

## Current limitations

The following planned components are not implemented yet:

- Crawl4AI collection
- Competitor discovery and competitor business APIs
- LangGraph workflow orchestration
- Deep Agents strategic interpretation
- Clerk JWT verification
- External business APIs, GitHub monitoring, social monitoring, and hiring monitoring
- Redis, Celery, Pinecone, and durable background-job recovery
