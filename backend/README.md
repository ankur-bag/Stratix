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

Configure crawling with the `AUTO_START_COMPETITOR_BASELINE`, `COMPETITOR_CRAWLER_*`, and `CHANGE_*` values documented in `.env.example`. Defaults collect one page per source, one request at a time per domain, and at most 12 sources. Crawling still has no anti-bot bypass or authenticated-page support; Phase 5 adds durable execution and scheduled monitoring around the existing public-source workflow.

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

## Durable jobs and scheduled monitoring

Production API processes validate mutations, persist the user-facing domain record, enqueue a MongoDB-backed job, and return `202 Accepted`. FastAPI does not execute long-running initialization, competitor scans, PDF ingestion, rebuilds, or cleanup work directly in production. At least one separate worker process is required for queued work, and at least one scheduler process is required for automatic monitoring.

`background_jobs` is the durable execution ledger. Each record is Organization-scoped and references its public domain record through `domain_resource_type` and `domain_resource_id`; the domain-specific initialization, scan, and ingestion endpoints remain the UI source of truth. Queue payloads contain only IDs and small versioned parameters—never PDF/page text, embeddings, credentials, or Clerk tokens.

Workers atomically claim one eligible job with `find_one_and_update`, ordered by priority, availability, and creation time. A claim records the worker ID and a time-bounded lease. The owning worker renews that lease while the handler runs, and every progress/final-state mutation matches both job and worker. A result is discarded if ownership changes. Expired claims are recovered at worker startup and periodically: jobs with attempts remaining return to `retry_scheduled`; exhausted jobs become `dead_lettered`, and associated domain records are reconciled so they do not stay stuck in a running state.

Retryable dependency failures use bounded exponential backoff with testable jitter and persist a future `available_at`; workers never sleep for the retry delay. Permanent validation/ownership/configuration failures fail immediately. Manual retry creates a new queue record and preserves the prior record as audit history. Queued cancellation is immediate; running cancellation becomes `cancel_requested` and is honored at safe handler checkpoints.

Concurrency is layered across the worker, job type, Organization, and competitor. Active-job, queued-scan, and global-queue limits provide backpressure. The approximate-fair claim strategy avoids repeatedly selecting one Organization while another has runnable work. When scheduled work hits backpressure, the scheduler defers it for 15 minutes, records a safe degraded reason, and does not silently drop the occurrence.

Monitoring schedules are stored in `monitoring_schedules`. New approved competitors default to the configured weekly cadence. Users can choose manual, daily, every three days, or weekly; the configured minimum interval prevents sub-daily scheduling. The scheduler atomically reserves due schedules and uses `schedule:{schedule_id}:{scheduled_timestamp}` as the occurrence key, so concurrent schedulers cannot enqueue the same occurrence twice. A manual/scheduled collision reuses the active competitor-level queue key instead of crawling twice.

Final failed jobs and successful scans with new signals create deduplicated, Organization-scoped in-app notifications. Retry attempts and no-change scans do not create noise. `notifications` has bounded cursor reads, indexed unread counts, read-one/read-all mutations, and TTL plus durable-retention cleanup.

Daily Organization-scoped `retention_cleanup` jobs expire abandoned uploads, enqueue canonical storage deletion, remove old successful/cancelled queue history after `JOB_CLEANUP_RETENTION_DAYS`, retain failed/dead-letter history for `DEAD_LETTER_RETENTION_DAYS`, remove old notifications, and prune expired service heartbeats in bounded batches. User-requested document deletion already enqueues separate idempotent Pinecone and object-storage cleanup jobs; handlers re-load the canonical Organization-owned document and derive the Pinecone namespace server-side.

### MongoDB indexes

Phase 5 adds queue indexes for claim order, expired leases, Organization/status and Organization/type/status queries, domain history, worker ownership, and completion time. Active `(organization_id, deduplication_key)` values use a partial unique index. Schedules have a unique Organization/competitor index plus a due/reservation index. Notifications have Organization/time, Organization/unread/time, Organization/deduplication, and expiry indexes. Service heartbeats are indexed by service type and freshness.

### Processes

Run these from `backend/` in separate terminals:

```powershell
# API
uv run fastapi dev app/main.py

# Durable worker
uv run python -m app.worker

# Monitoring scheduler
uv run python -m app.scheduler
```

Keep `EMBEDDED_WORKER_ENABLED=false` and `EMBEDDED_SCHEDULER_ENABLED=false` in production. The API, worker, and scheduler share MongoDB and provider configuration, but are independently deployable processes. Enable `REQUIRE_ACTIVE_WORKER` or `REQUIRE_ACTIVE_SCHEDULER` only when readiness should fail without a fresh corresponding service heartbeat.

Read-only diagnostics:

```powershell
uv run python scripts/check_workers.py status
uv run python scripts/check_workers.py queue
uv run python scripts/check_workers.py leases
uv run python scripts/check_workers.py dead-letters
uv run python scripts/benchmark_job_queue.py
```

`GET /health/ready` reports worker/scheduler availability, queue degradation, oldest queued age, expired leases, and dead-letter count without exposing hosts or secrets. With the `REQUIRE_ACTIVE_*` defaults set to false, a separately deployed service being temporarily absent is diagnostic rather than an API readiness failure.

### Job and notification APIs

- `GET /api/v1/jobs` and `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `POST /api/v1/jobs/{job_id}/retry`
- `GET/PATCH /api/v1/competitors/{competitor_id}/monitoring-schedule`
- `POST /api/v1/competitors/{competitor_id}/monitoring-schedule/run-now`
- `GET /api/v1/notifications` and `GET /api/v1/notifications/unread-count`
- `POST /api/v1/notifications/{notification_id}/read`
- `POST /api/v1/notifications/read-all`

All routes derive tenancy from the verified Clerk Organization. Organization IDs are never accepted in request bodies, and cross-Organization resources return not found.

## Unified competitor intelligence evidence

Phase 6 projects canonical competitor snapshots and objective signals into the
`intelligence_evidence` MongoDB collection. Snapshot sections are normalized into
deterministic, heading-aware chunks; signal projections retain bounded summaries and
before/after evidence. MongoDB remains the source of truth. Pinecone stores only
768-dimensional vectors and bounded identifiers/classifications in a namespace derived
from the verified Clerk Organization.

Indexing writes a new version inactive, embeds it, requires acknowledgement for every
vector upsert, then activates the Mongo version and removes stale vectors. A failed
projection leaves the prior active version searchable. Successful scans enqueue snapshot
and signal indexing as optional durable follow-up work; indexing/provider failure cannot
change an already successful scan result. Workspace reindexing reuses canonical stored
sources and never recrawls websites or recreates signals.

Intelligence APIs:

- `POST /api/v1/intelligence/search`
- `POST /api/v1/intelligence/answer`
- `GET /api/v1/competitors/{competitor_id}/timeline`
- `GET /api/v1/intelligence/overview`
- `GET /api/v1/intelligence/trends`
- `POST /api/v1/intelligence/reindex`
- `GET /api/v1/intelligence/indexing-status`

Search embeds the query, applies server-derived namespace and metadata filters, then
re-fetches every match from MongoDB and revalidates tenancy, activity, deletion state,
competitor filters, source filters, and date filters. Responses contain bounded excerpts
and evidence IDs, never full pages, PDF bodies, vectors, prompts, or provider errors.
Answer mode is deterministic by default, separates observations from inference, and
only cites evidence returned in the same bundle. It does not produce recommendations or
invoke external tools.

The collection supports snapshot chunks, deterministic signal summaries, company
document references, and reserved bounded kinds for future structured facts and
before/after projections. Current collectors produce website snapshot chunks and signal
summaries; hiring and GitHub source types are schema-compatible but remain uncollected
until an approved source collector exists. Company PDF text stays in its existing
canonical chunk collection and is referenced at retrieval time rather than duplicated.

Correlation is rule-based and Organization-scoped: it can identify multi-source support,
same-category movement across approved competitors, repeated feature/positioning
activity, and repeated numeric pricing direction. Trend windows are fixed to 30, 90,
180, or 365 days and report observed counts, pricing direction, scan coverage, and
freshness without prediction or statistical-significance claims. Structured synthesis
has a strict optional adapter with citation validation and deterministic fallback; no
production generation provider is wired in Phase 6.

Read-only diagnostics and the offline search benchmark:

```powershell
uv run python scripts/check_intelligence_index.py status
uv run python scripts/check_intelligence_index.py consistency --organization-id org_example
uv run python scripts/check_intelligence_index.py sample --organization-id org_example
uv run python scripts/benchmark_intelligence_search.py
```

The diagnostic never mutates Pinecone or MongoDB. Live indexing and reindexing require
explicit provider configuration and an authorized Organization. `GET /health/ready`
reports whether embeddings and the vector store are available; it fails only when
`COMPETITOR_EVIDENCE_INDEXING_REQUIRED=true` and the required providers are unavailable.

## Phase 7 Strategic Deep Agent

Phase 7 adds an evidence-grounded reporting layer without expanding collection or action
permissions:

```text
authenticated strategic request
-> Organization-scoped strategy_run + durable strategic_analysis job
-> deterministic LangGraph retrieval/analyze/validate/persist workflow
-> Phase 6 company context, evidence search, timelines, trends, and correlations
-> Deep Agents top-level analyst with at most four bounded specialists
-> strict Pydantic report and canonical citation reauthorization
-> immutable strategy_report + deduplicated terminal notification
```

LangGraph owns the deterministic outer workflow, durable job state, progress,
cancellation, retry, lease-loss checks, and idempotent persistence. Deep Agents 0.7.1
owns only bounded planning and delegation. The configured Gemini chat model is provided
through `langchain-google-genai`; the embedding client is not reused as a chat model.
Normal tests use `FakeStrategicAgent` or `DisabledStrategicAgent` and make no provider
calls.

The top-level analyst may delegate to Product & Pricing, Engineering & Product Signals,
GTM & Positioning, and Opportunity & Risk specialists. General-purpose recursive
subagents are disabled. The registered Google harness excludes `ls`, `read_file`,
`write_file`, `edit_file`, `glob`, `grep`, and `execute`. The only application tools are:

- `get_company_context`
- `get_competitor_profiles`
- `search_intelligence_evidence`
- `get_evidence_details`
- `get_competitor_timeline`
- `get_intelligence_trends`
- `get_intelligence_correlations`
- `compare_competitors`

These tools receive Organization, company, user, analysis cutoff, competitor allowlist,
evidence allowlist, cancellation state, and budgets from immutable server context, not
model arguments. Every call checks the durable job lease and cancellation state,
validates competitors, applies date/result/context limits, and reauthorizes canonical
MongoDB records. No tool exposes MongoDB, Pinecone namespaces or vectors, credentials,
storage paths, raw PDFs, full pages, crawlers, Tavily, arbitrary URLs, network clients,
the host filesystem, a shell, or mutation actions.

Prompts treat retrieved text as untrusted data. Evidence containing instructions cannot
alter tool permissions or tenancy. Every substantive observation, inference, comparison,
threat, opportunity, and recommendation requires bounded evidence IDs. Recommendations
also require declared company-context source IDs, assumptions, risks, success
indicators, and bounded impact/effort/horizon/confidence values. After at most one
schema-only repair, citations are reloaded in one canonical batch and checked for
Organization, company, competitor, active/deleted state, date range, and immutable
`analysis_as_of`. Invalid output is rejected. When evidence or the configured model is
unavailable, the worker may persist a clearly labeled deterministic evidence summary
with uncertainties and gaps; it never fabricates recommendations.

The canonical collections are `strategy_runs`, `strategy_reports`, and
`strategy_report_feedback`. Run indexes cover Organization/time,
Organization/status/time, Organization/request-hash/status,
Organization/background-job, company/time, and retention on `completed_at`. Report
indexes cover Organization/time, unique Organization/run, Organization/company/time, and
retention on `created_at`. Feedback has a unique Organization/report/user index and an
Organization/time index. Successful reports are created once per run and otherwise
immutable except their explicit archival field.

The API surface is:

- `POST /api/v1/strategy/runs`
- `GET /api/v1/strategy/runs`
- `GET /api/v1/strategy/runs/{run_id}`
- `POST /api/v1/strategy/runs/{run_id}/cancel`
- `POST /api/v1/strategy/runs/{run_id}/retry`
- `GET /api/v1/strategy/reports`
- `GET /api/v1/strategy/reports/{report_id}`
- `GET /api/v1/strategy/reports/{report_id}/citations/{evidence_id}`
- `POST /api/v1/strategy/reports/{report_id}/feedback`

Enable the feature only after setting the Strategic Deep Agent block in `.env.example`
with a supported model and the existing server-only `GEMINI_API_KEY`. Configuration
validation rejects an enabled feature with an empty model and enforces all subagent,
tool, iteration, timeout, evidence, context, competitor, concurrency, lookback, repair,
and retention limits. `GET /health/ready` reports cached provider configuration plus
active/oldest queued/failed runs, citation failures, average retrieved evidence, and
budget exhaustion; it never calls the model.

Read-only and offline-safe diagnostics:

```powershell
uv run python scripts/check_strategic_agent.py status
uv run python scripts/check_strategic_agent.py configuration
uv run python scripts/check_strategic_agent.py runs --organization-id org_example
uv run python scripts/benchmark_strategic_agent.py
uv run python scripts/evaluate_strategic_agent.py
```

The optional provider connectivity check is separate and requires both
`--live-provider-smoke` and `STRATIX_LIVE_STRATEGIC_TESTS=true`. It uses no company data.
Do not enable it in automated tests.

Run the complete application as separate processes:

```powershell
# API
uv run fastapi dev app/main.py

# durable worker (required for strategic analysis)
uv run python -m app.worker

# scheduled monitoring
uv run python -m app.scheduler
```

The API never silently executes a strategic analysis. The agent does not crawl websites
directly; it operates only on evidence already collected, authorized, and indexed by
Stratix. Recommendations are advisory and are never executed automatically. Production
billing, messaging integrations, authenticated crawling, GitHub collection, and
autonomous actions remain out of scope. See
[`docs/phase7-live-mvp-smoke.md`](docs/phase7-live-mvp-smoke.md) for the complete
authorized live verification and recovery procedure.

### Phase 6 authorized smoke test

Run the API, worker, and scheduler as separate processes, then:

1. Sign in through Clerk and select the Avento Organization.
2. Approve a competitor and run a competitor scan.
3. Confirm snapshots and any deterministic signals are persisted.
4. Confirm snapshot/signal indexing jobs are queued and completed by the worker.
5. Confirm vector count increases only in Avento's derived namespace.
6. Search `/intelligence` for a known competitor-page fact and verify excerpt, date,
   competitor, and public source citation.
7. Ask a historical comparison question and confirm every cited ID is in the returned
   evidence bundle.
8. Open the competitor timeline, page backward, and verify signal before/after excerpts.
9. Switch Organizations and confirm prior evidence, answers, timeline, jobs, and
   notifications disappear before the new workspace loads.
10. Queue a reindex in the second Organization and verify it reuses stored snapshots and
    signals without crawling or recreating changes.
11. Deactivate an authorized test evidence source, verify MongoDB retrieval eligibility
    changes immediately, then confirm eventual vector cleanup.
12. Stop a worker during indexing, wait for lease expiry, restart it, and verify recovery
    reuses deterministic IDs without duplicate active versions.

This live flow was not executed by the offline automated suite. Do not run it against
production data or provider accounts without explicit authorization.

### Migration and recovery

Inspect legacy active initialization, scan, and ingestion records before enabling durable workers:

```powershell
uv run python scripts/migrate_background_jobs.py status
uv run python scripts/migrate_background_jobs.py plan
```

Both commands are read-only. Applying is explicit and idempotent:

```powershell
uv run python scripts/migrate_background_jobs.py apply --confirm-apply
```

Do not run `apply`, cancellation, retry, or destructive cleanup against real data without confirming the target environment. Old completed domain records remain readable; migration only creates missing durable queue references for legacy queued/running records.

### Current limitations

There is no email or Slack delivery, user-configurable alert routing, sub-daily monitoring, authenticated crawling, anti-bot bypass, or exactly-once guarantee for arbitrary external providers. Idempotency is enforced through queue occurrence keys plus the existing deterministic vector IDs, snapshot uniqueness, signal uniqueness, and notification deduplication. The offline suite does not prove live Clerk, MongoDB failover, Crawl4AI, Gemini, Pinecone, S3, or process-kill behavior; run the documented manual flow in an authorized non-production environment. Phase 6 provides evidence retrieval and historical intelligence; strategic recommendations and autonomous Deep Agent reasoning are reserved for Phase 7.
