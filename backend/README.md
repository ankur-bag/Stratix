# Stratix Backend

Stratix is a strategic intelligence platform for startups and product teams. It is
designed to monitor public competitor activity, detect objective changes, and turn
verified signals into evidence-backed strategic insights.

## Phase 2 status

Phase 2 preserves the Phase 0 and Phase 1 foundation and adds the company intelligence
bootstrap:

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
- Crawl4AI behind a replaceable application-owned crawler interface
- Centralized DNS, redirect, same-domain, robots, timeout, retry, and size safeguards
- Deterministic page normalization and SHA-256 page/section hashing
- Organization-scoped baseline snapshots and observed intelligence profiles
- A typed LangGraph initialization workflow
- Deterministic and optional structured-model extraction boundaries
- Idempotent company reinitialization without deleting historical snapshots
- Replaceable background-execution boundary
- Offline tests using in-memory persistence and background-execution fakes
- Ruff, mypy, and pytest development tooling

The onboarding request still returns `202 Accepted` as soon as its company profile,
website source, initialization job, and idempotency result are persisted. The
background workflow then analyzes only the user's registered public company website.
Competitor discovery and continuous monitoring remain out of scope.

## Technology stack

- Python 3.12
- FastAPI
- Pydantic and pydantic-settings
- PyMongo `AsyncMongoClient` (no ODM)
- Crawl4AI 0.9
- LangGraph
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
|   |-- crawlers/           # Crawl4AI adapter, policy, and crawler schemas
|   |-- domain/             # Company, snapshot, and intelligence models
|   |-- graphs/             # Typed company initialization LangGraph
|   |-- repositories/       # Persistence protocols and PyMongo implementations
|   |-- services/           # Onboarding, normalization, extraction, and execution
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

Install Crawl4AI's local browser runtime once after dependency synchronization:

```bash
uv run crawl4ai-setup
```

This downloads browser assets for real local crawls. The offline test suite does not
run this command and does not require browser downloads.

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

MongoDB, development-authentication, and crawler settings are:

```dotenv
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=stratix
MONGODB_CONNECT_TIMEOUT_MS=5000
MONGODB_SERVER_SELECTION_TIMEOUT_MS=5000
AUTH_DISABLED=true
DEV_USER_ID=user_development
DEV_ORGANIZATION_ID=org_development
CRAWLER_USER_AGENT=StratixBot/0.1
CRAWLER_MAX_DEPTH=2
CRAWLER_MAX_PAGES=15
CRAWLER_MAX_CONCURRENCY_PER_DOMAIN=1
CRAWLER_MIN_DELAY_SECONDS=4
CRAWLER_MAX_DELAY_SECONDS=8
CRAWLER_MAX_RETRIES=2
CRAWLER_PAGE_TIMEOUT_MS=45000
CRAWLER_MAX_RESPONSE_BYTES=5000000
CRAWLER_CHECK_ROBOTS=true
LLM_ENABLED=false
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

The typed LangGraph workflow executes these persistence boundaries:

```text
validate company and source
-> crawl the bounded company website
-> normalize and hash usable pages
-> persist baseline snapshots
-> extract and persist company intelligence
-> mark the source and company ready
-> complete the initialization job
```

Progress is persisted during validation, crawling, normalization, baseline storage,
context extraction, and readiness. Partial page failures are retained while usable
pages continue. If extraction fails after snapshots are saved, those snapshots remain
available for diagnosis or reinitialization.

Local development uses an application-managed asynchronous task. The job is persisted
before scheduling and the HTTP response does not wait for initialization. This task
mechanism is not durable: a process restart may interrupt a running job. The executor
boundary is designed to be replaced by a durable queue in a later phase.

## Crawler safety policy

Real crawls accept only public HTTP(S) targets on the registered company domain. The
policy rejects credentials, loopback/private/link-local/metadata destinations,
validates DNS immediately before access, validates redirect destinations, filters
cross-domain links, and enforces depth, page, timeout, response-size, retry, delay, and
single-domain concurrency limits. Crawl4AI robots checking is enabled by default.

Persistent `403`, `429`, CAPTCHA, access-denied, or robots-denied responses stop safely
with stable error codes. Stratix does not rotate proxies or bypass CAPTCHA, login,
paywall, robots, or other access controls. Crawled content is untrusted input.

## Intelligence extraction

With the default `LLM_ENABLED=false`, Stratix uses a deterministic extractor based on
stored titles, metadata, headings, page types, and declared onboarding context. It
makes no model or API call.

An optional structured-model extractor can be injected when `LLM_ENABLED=true`. Its
Pydantic output must reference snapshot IDs from the current baseline; unknown or
fabricated references fail initialization. Deep Agents are not used in Phase 2.

## Phase 2 API additions

- `GET /api/v1/company/intelligence-profile` returns observed public-site context.
- `GET /api/v1/company/snapshots` returns organization-scoped baseline snapshots.
- `POST /api/v1/company/reinitialize` requires `Idempotency-Key`, reuses the existing
  source, preserves old snapshots, creates a new job, and returns `202 Accepted`.

Reinitialization returns `INITIALIZATION_ALREADY_RUNNING` while another queued or
running initialization job exists. Reusing the same idempotency key returns the
original job without creating duplicates.

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

- Competitor discovery and competitor business APIs
- Continuous monitoring and deterministic before/after change detection
- Deep Agents strategic interpretation
- Clerk JWT verification
- External business APIs, GitHub monitoring, social monitoring, and hiring monitoring
- Redis, Celery, Pinecone, and durable background-job recovery

Real crawling additionally requires the local Crawl4AI browser setup and public DNS
and network access. Tests use fake DNS, crawlers, repositories, executors, and models;
they never access a live website or MongoDB server.
