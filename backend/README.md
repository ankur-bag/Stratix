# Stratix Backend

Stratix is an evidence-backed competitive-intelligence platform for startup and product teams. The backend builds a public company context, discovers likely competitors with traceable search evidence, and persists user-approved competitors for later monitoring.

## Phase 3 status

Phase 3 preserves the Phase 0–2 FastAPI, PyMongo, onboarding, Crawl4AI, normalization, snapshot, extraction, and LangGraph foundation. It adds:

- Clerk session-token verification and Clerk Organization tenancy
- Explicit browser CORS configuration
- Persisted, non-blocking competitor-discovery jobs
- Deterministic query generation, URL safety, evidence merging, and ranking
- Optional Tavily search behind an application-owned interface
- An optional, single structured classification boundary when `LLM_ENABLED=true`
- Candidate filtering, pagination, approval, and rejection
- Approved competitors and pending initial website sources
- Optional automatic discovery after company initialization
- Offline authentication and discovery tests

Continuous competitor collection, rescanning, change detection, signals, and Deep Agents are not part of Phase 3.

## Stack

- Python 3.12 and uv
- FastAPI and Pydantic
- PyMongo `AsyncMongoClient`
- Clerk `clerk-backend-api`
- Crawl4AI and LangGraph
- Tavily Python SDK and `tldextract`
- Ruff, mypy, pytest, and HTTPX

## Setup

Install uv, then synchronize the locked environment:

```powershell
cd backend
uv sync --dev
Copy-Item .env.example .env
```

For real company-site crawling, install Crawl4AI's browser assets once:

```powershell
uv run crawl4ai-setup
```

Never commit `.env`, Clerk keys, Tavily keys, MongoDB credentials, session tokens, or other secrets.

## Clerk authentication

The frontend sends the default Clerk session token as `Authorization: Bearer <token>`. The backend verifies it with the official Python SDK, accepts only `session_token`, validates authorized parties, and reads the trusted `sub`, `sid`, `org_id`, `org_role`, and `org_permissions` claims from the SDK-normalized payload.

Configure:

```dotenv
APP_ENV=development
AUTH_DISABLED=false
CLERK_SECRET_KEY=
CLERK_JWT_KEY=
CLERK_AUTHORIZED_PARTIES=["http://localhost:3000"]
CLERK_REQUIRE_ORGANIZATION=true
FRONTEND_ALLOWED_ORIGINS=["http://localhost:3000"]
```

`CLERK_JWT_KEY` is Clerk's PEM public key. Providing it enables networkless JWT signature verification; `CLERK_SECRET_KEY` remains available to the SDK for configurations that require Clerk's Backend API. Authorized parties must contain the exact frontend origins that may issue tokens for this API.

All `/api/v1` routes require a verified user and active Organization. Health endpoints remain public and expose no provider details. Viewing and reviewing candidates requires Organization membership, not an administrator role. A reusable admin dependency exists for future destructive administrative operations.

Development authentication is available only with both `APP_ENV=development` and `AUTH_DISABLED=true`; it uses `DEV_USER_ID` and `DEV_ORGANIZATION_ID`. Production rejects disabled authentication. Production readiness returns `AUTH_CONFIGURATION_INVALID` when neither a Clerk JWT key nor secret key is configured, or authorized parties are empty. API documentation is controlled by `DOCS_ENABLED`.

## CORS

`FRONTEND_ALLOWED_ORIGINS` is an explicit JSON list. Browser preflight permits `Authorization`, `Content-Type`, `Idempotency-Key`, and `X-Request-ID`. Wildcard origins are rejected in production. CORS does not weaken Clerk verification.

## Competitor discovery

Discovery runs through a replaceable, non-durable background executor:

```text
ready company context
→ deterministic bounded queries
→ provider searches
→ safe domain normalization and evidence merging
→ deterministic scoring and ranking
→ optional one-call structured classification
→ candidate persistence
→ user review
→ competitor and pending website-source persistence
```

Every candidate retains source queries, result URLs, snippets, provider scores, matching context, reasons, score, and confidence. The user's own domain, unsafe destinations, duplicate registrable domains, and common non-company directory domains are excluded. Rejected evidence is retained for auditability.

Configure discovery with:

```dotenv
AUTO_START_COMPETITOR_DISCOVERY=true
DISCOVERY_MAX_QUERIES=8
DISCOVERY_MAX_RESULTS_PER_QUERY=8
TAVILY_API_KEY=
LLM_ENABLED=false
```

Without `TAVILY_API_KEY`, no live search is attempted and a scheduled job ends with a stable provider-unavailable error. Tests inject offline providers. `LLM_ENABLED=false` always uses deterministic ranking and performs no model call. Phase 3 includes only the structured classifier interface; it does not configure a production model or use Deep Agents.

The in-process executor is not durable. A process restart can interrupt initialization or discovery jobs; persisted progress remains available, but automatic recovery requires a later durable queue.

## API

Public:

- `GET /health/live`
- `GET /health/ready`

Authenticated company bootstrap:

- `POST /api/v1/onboarding/company`
- `GET /api/v1/onboarding/status`
- `GET /api/v1/initialization-jobs/{job_id}`
- `GET/PATCH /api/v1/company`
- `GET /api/v1/company/intelligence-profile`
- `GET /api/v1/company/snapshots`
- `POST /api/v1/company/reinitialize`

Authenticated discovery and review:

- `POST /api/v1/competitor-discovery/jobs` with `Idempotency-Key`
- `GET /api/v1/competitor-discovery/jobs/latest`
- `GET /api/v1/competitor-discovery/jobs/{job_id}`
- `GET /api/v1/competitor-candidates`
- `GET /api/v1/competitor-candidates/{candidate_id}`
- `POST /api/v1/competitor-candidates/review`
- `GET /api/v1/competitors`

Candidate listing supports `status`, `relationship_type`, `minimum_score`, `discovery_job_id`, `cursor`, and bounded `limit` filters. Cross-Organization resources appear not found.

## Run and validate

```powershell
uv run fastapi dev app/main.py
```

Run offline quality checks from `backend/`:

```powershell
uv run ruff format .
uv run ruff check .
uv run mypy app
uv run pytest
git diff --check
```

Tests use in-memory repositories and fake Clerk, crawler, search-provider, executor, and model boundaries. They require no Clerk network access, Tavily, MongoDB, live websites, DNS, browser downloads, or model credentials.
