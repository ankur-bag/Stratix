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

## Phase 4 monitoring

Phase 4 adds the objective competitor-monitoring loop while preserving the Phase 0–3 behavior:

```text
approved competitor
→ initial website source
→ bounded source discovery
→ persisted baseline scan
→ later manual rescan
→ deterministic normalization and section comparison
→ evidence-linked objective signals
```

The first successful scan establishes baseline snapshots and never creates a change signal. An identical later scan records job progress but does not duplicate the full snapshot or create a signal. Distinct normalized content is versioned and linked to its previous snapshot; only changes above the configured deterministic threshold create signals. Signal types use page type, section additions/removals, and structured numeric changes. They do not contain strategic interpretation, intent, opportunity analysis, or recommendations.

Source discovery starts from the approved canonical domain, retains same-domain public HTML pages, removes tracking parameters and fragments, rejects account/private/file routes, and stays within `COMPETITOR_CRAWLER_MAX_SOURCES`. Every submitted, discovered, and redirected URL passes the existing SSRF and DNS policy. Robots denial and explicit access denial are not bypassed; blocked sources remain visible with a user-safe status.

Competitor scans use LangGraph for serializable state, progress transitions, partial-source handling, snapshot persistence, deterministic comparison, signal construction, and competitor-state updates. Jobs run through a replaceable in-process executor. It is intentionally non-durable: a process restart can interrupt queued or running work, and Phase 4 does not automatically recover it.

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
CLERK_AUTHORIZED_PARTIES=["http://localhost:3000","http://localhost:3002"]
CLERK_CLOCK_SKEW_MS=10000
CLERK_REQUIRE_ORGANIZATION=true
FRONTEND_ALLOWED_ORIGINS=["http://localhost:3000","http://localhost:3002"]
```

`CLERK_JWT_KEY` is Clerk's PEM public key. Providing it enables networkless JWT signature verification; `CLERK_SECRET_KEY` remains available to the SDK for configurations that require Clerk's Backend API. Authorized parties must contain the exact frontend origins that may issue tokens for this API.

All `/api/v1` routes require a verified user and active Organization. Health endpoints remain public and expose no provider details. Viewing and reviewing candidates requires Organization membership, not an administrator role. A reusable admin dependency exists for future destructive administrative operations.

## Multi-company tenancy

One Clerk user may belong to multiple Organizations. One Clerk Organization maps to exactly one Stratix company profile, and the verified `org_id` claim is the only tenant selector. The API never accepts a company or Organization ID from the browser to choose tenancy. The company-profile index is unique by `organization_id`; competitors, sources, jobs, snapshots, idempotency records, scans, and signals are queried and indexed with that same Organization boundary. Identifiers from another Organization return not found.

The frontend must activate the selected Clerk Organization and mint a fresh token for that Organization before calling this API. Switching companies requires no backend migration and must not use a cross-company aggregation endpoint. Keep `CLERK_REQUIRE_ORGANIZATION=true` and explicitly configure every allowed frontend origin in both `CLERK_AUTHORIZED_PARTIES` and `FRONTEND_ALLOWED_ORIGINS`.

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

## Private company PDF knowledge

Phase 4.5 accepts private, text-based PDFs during onboarding and from Settings. The browser requests an opaque upload target, uploads directly to local private storage or an S3-compatible bucket, and completes the upload with its size and SHA-256 digest. The API then validates and extracts the PDF with PyMuPDF, creates deterministic page-aware chunks in MongoDB, optionally embeds them with Gemini, and stores only 768-dimensional vectors plus bounded identifiers in the existing Pinecone index.

MongoDB remains canonical for document metadata and chunk text. Pinecone contains no full text and uses one server-derived namespace per verified Clerk Organization (`stratix-org-{org_id}`). Search always queries that namespace, then rechecks each active chunk and document against MongoDB before returning canonical excerpts. The API never accepts an Organization or Pinecone namespace from the browser.

The initial limits are one optional onboarding PDF, 20 MB, 100 pages, and ten documents per company. Encrypted, malformed, image-only, and over-limit PDFs fail with stable document error codes. OCR, office files, URLs as document input, and client-selected namespaces are not supported. Failed reprocessing keeps the previous active version searchable.

`DOCUMENT_STORAGE_PROVIDER=local` is the private development default and writes under the ignored `DOCUMENT_STORAGE_PATH`. Set it to `s3` with a private bucket for presigned uploads. Embeddings and vector search are disabled by default. To enable them, configure the Gemini and Pinecone placeholders in `.env.example`; `PINECONE_HOST` is required for data operations. Startup validates the existing `stratix-knowledge` index dimension (768), metric (cosine), and readiness and never creates or deletes an index. Optional vector failure degrades document search while Mongo-backed company knowledge remains usable; `VECTOR_SEARCH_REQUIRED=true` makes readiness fail instead.

Document APIs:

- `POST /api/v1/company-documents/upload-requests`
- `PUT /api/v1/company-documents/{document_id}/content` (local development)
- `POST /api/v1/company-documents/{document_id}/complete`
- `GET /api/v1/company-documents` and `GET /api/v1/company-documents/{document_id}`
- `DELETE /api/v1/company-documents/{document_id}`
- `POST /api/v1/company-documents/{document_id}/reprocess`
- `GET /api/v1/document-ingestion-jobs/{job_id}`
- `POST /api/v1/company/knowledge/search`

The offline benchmark and Pinecone diagnostic are available as:

```powershell
uv run python scripts/benchmark_document_ingestion.py
uv run python scripts/check_pinecone.py
uv run python scripts/check_pinecone.py --smoke-test
```

The diagnostic only describes and validates the configured manual index. Its optional smoke test uses a temporary namespace and deletes only its temporary vectors; it never creates or deletes an index. Live Gemini, Pinecone, S3, and private-document smoke tests require explicit credentials and authorization and were not run by the automated suite.

Add actual provider secrets manually only to `backend/.env`; never commit that
file. `GEMINI_API_KEY`, `PINECONE_API_KEY`, and `PINECONE_HOST` must not appear
in `frontend/.env.local`, any `NEXT_PUBLIC_*` variable, browser code, or API
responses. Copy the complete index host from Pinecone Console; do not copy a
shortened host shown in a screenshot.

Example provider configuration (placeholders only):

```dotenv
GEMINI_API_KEY=
EMBEDDINGS_ENABLED=true
EMBEDDING_PROVIDER=gemini
EMBEDDING_MODEL=gemini-embedding-001
EMBEDDING_DIMENSIONS=768
EMBEDDING_DOCUMENT_TASK_TYPE=RETRIEVAL_DOCUMENT
EMBEDDING_QUERY_TASK_TYPE=RETRIEVAL_QUERY

VECTOR_DB_PROVIDER=pinecone
VECTOR_SEARCH_ENABLED=true
VECTOR_SEARCH_REQUIRED=false
PINECONE_API_KEY=
PINECONE_INDEX_NAME=stratix-knowledge
PINECONE_HOST=
PINECONE_DIMENSIONS=768
PINECONE_METRIC=cosine
PINECONE_NAMESPACE_PREFIX=stratix-org-
```

### Authorized manual smoke test

Run this only with explicitly authorized provider accounts and private files:

1. Start the backend and frontend.
2. Sign in with Clerk.
3. Select the Avento company.
4. Upload a valid Avento PDF during onboarding.
5. Confirm upload completion.
6. Confirm document processing reaches `ready`.
7. Confirm MongoDB contains the canonical chunks.
8. Confirm Pinecone contains records only in Avento's Organization namespace.
9. Confirm the Pinecone record count increases.
10. Search for an Avento product or customer-segment question.
11. Confirm relevant PDF evidence is returned.
12. Confirm the evidence includes page references.
13. Switch to another company.
14. Confirm Avento documents and search results disappear.
15. Upload a document for the second company.
16. Confirm a separate Pinecone namespace appears.
17. Switch back to Avento.
18. Reprocess the Avento PDF.
19. Confirm no duplicate active version remains.
20. Delete the PDF.
21. Confirm it immediately disappears from semantic search.
22. Confirm Pinecone cleanup completes.
23. Confirm website intelligence remains available.

This manual flow was not executed by the offline automated suite.

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

Authenticated competitor monitoring:

- `GET /api/v1/competitors/{competitor_id}`
- `GET/POST /api/v1/competitors/{competitor_id}/sources`
- `PATCH/DELETE /api/v1/competitors/{competitor_id}/sources/{source_id}`
- `POST /api/v1/competitors/{competitor_id}/sources/discover` with `Idempotency-Key`
- `POST /api/v1/competitors/{competitor_id}/scans` with `Idempotency-Key`
- `GET /api/v1/competitor-scans/{scan_id}`
- `GET /api/v1/competitors/{competitor_id}/scans`
- `GET /api/v1/competitors/{competitor_id}/signals`
- `GET/PATCH /api/v1/signals/{signal_id}`

Signal listing supports `type`, `status`, `minimum_importance`, `source_id`, observation-date, `scan_id`, cursor, and bounded-limit filters. Signal PATCH accepts only `reviewed` or `dismissed`; evidence and facts are immutable through the API.

Configure Phase 4 with the `AUTO_START_COMPETITOR_BASELINE`, `COMPETITOR_CRAWLER_*`, and `CHANGE_*` values documented in `.env.example`. Defaults collect one page per source, one request at a time per domain, and at most 12 sources. There is no cron scheduler, durable queue, anti-bot bypass, authenticated crawling, or continuous monitoring in this phase.

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

Tests use in-memory repositories and fake Clerk, crawler, search-provider, executor, and model boundaries. Multi-company tests exercise two Organizations owned by one user, Organization-scoped idempotency, profiles, jobs, competitors, sources, scans, and signals, including cross-Organization not-found behavior. They require no Clerk network access, Tavily, MongoDB, live websites, DNS, browser downloads, or model credentials.

For a manual local smoke test, approve a candidate, open `/competitors/{competitor_id}`, confirm the initial website source, create the baseline, and wait for the terminal status. Run the same fake/static page again to verify the no-change outcome, then change a pricing or feature section and rescan to verify before/after evidence. Live crawling and production data changes must be authorized separately.
