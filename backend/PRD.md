# Stratix Backend Product Requirements Document

**Document type:** Implementation PRD  
**Audience:** Codex, GitHub Copilot, and backend contributors  
**Status:** Approved for backend MVP implementation  
**Primary stack:** Python 3.12, FastAPI, uv, MongoDB, Crawl4AI, LangGraph, Deep Agents

---

## 1. Product summary

Stratix is an AI-native strategic intelligence platform for startups and product teams. It monitors public competitor activity, converts objective changes into structured signals, and generates evidence-backed strategic insights.

The backend must distinguish three responsibilities:

1. **Collection:** Retrieve public source data through crawlers and official APIs.
2. **Detection:** Determine objective changes using deterministic code.
3. **Interpretation:** Use AI only to correlate verified signals and explain possible strategic meaning.

The backend must never treat model-generated inference as verified fact.

---

## 2. MVP objective

Build a production-shaped backend that supports one complete intelligence loop:

```text
Create competitor
→ register public sources
→ run manual scan
→ collect source data
→ normalize and hash content
→ compare with previous snapshot
→ create verified change signals
→ invoke strategic reasoning when meaningful signals exist
→ store evidence-backed insight
→ expose results through REST endpoints
```

The first implementation must prioritize correctness, traceability, and testability over the number of integrations.

---

## 3. MVP scope

### 3.1 Included

- FastAPI REST API
- Clerk-compatible bearer-token authentication boundary
- Organization/workspace isolation
- Competitor CRUD
- Website source registration
- Pricing, landing-page, changelog, and documentation source categories
- Crawl4AI-based public-page collection
- Conservative crawler policy
- Raw and normalized snapshot storage
- Deterministic content hashing
- Deterministic section-level change detection
- Signal classification
- LangGraph monitoring workflow
- Deep Agent strategic-analysis layer
- Evidence-backed insight persistence
- Manual scan execution
- Scan status retrieval
- Competitor signal feed
- Competitor insight feed
- Structured logging
- Unit and integration tests
- OpenAPI documentation

### 3.2 Deferred

- Twitter/X monitoring
- LinkedIn scraping
- General-purpose social scraping
- Hiring-portal scraping
- Reddit sentiment monitoring
- Browser extensions
- CRM integrations
- Automatic outbound actions
- Billing
- Predictive forecasting
- Pinecone
- Redis/Celery infrastructure
- Multi-cloud deployment
- Residential proxy rotation
- CAPTCHA bypassing
- Login-protected or private-source crawling

---

## 4. Technical decisions

### 4.1 Repository layout

The product repository may eventually contain both applications:

```text
stratix/
├── frontend/
└── backend/
```

This PRD applies to `backend/`.

Recommended backend layout:

```text
backend/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   │   ├── dependencies.py
│   │   ├── errors.py
│   │   └── v1/
│   │       ├── router.py
│   │       ├── competitors.py
│   │       ├── scans.py
│   │       ├── signals.py
│   │       └── insights.py
│   ├── agents/
│   │   ├── strategic_agent.py
│   │   ├── prompts.py
│   │   ├── schemas.py
│   │   └── tools.py
│   ├── core/
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── logging.py
│   │   ├── security.py
│   │   └── lifespan.py
│   ├── crawlers/
│   │   ├── base.py
│   │   ├── crawl4ai_client.py
│   │   ├── policies.py
│   │   └── schemas.py
│   ├── domain/
│   │   ├── competitors.py
│   │   ├── scans.py
│   │   ├── signals.py
│   │   ├── snapshots.py
│   │   └── insights.py
│   ├── graphs/
│   │   ├── monitoring.py
│   │   ├── state.py
│   │   └── routing.py
│   ├── repositories/
│   │   ├── competitors.py
│   │   ├── scans.py
│   │   ├── snapshots.py
│   │   ├── signals.py
│   │   └── insights.py
│   ├── services/
│   │   ├── change_detection.py
│   │   ├── normalization.py
│   │   ├── signal_classification.py
│   │   └── scanning.py
│   └── shared/
│       ├── ids.py
│       ├── time.py
│       └── types.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── conftest.py
├── .github/
│   └── copilot-instructions.md
├── AGENTS.md
├── PRD.md
├── CODEX_PROMPT.md
├── .env.example
├── pyproject.toml
├── uv.lock
└── README.md
```

Avoid creating empty modules that have no immediate role. Add folders as their first implementation is introduced.

### 4.2 Package management

- Use `uv` exclusively.
- Define runtime and development dependencies in `pyproject.toml`.
- Commit `uv.lock`.
- Do not add `requirements.txt` unless a deployment target explicitly requires an export.
- Run all project commands through `uv run`.

### 4.3 Python

- Target Python 3.12.
- Use modern type syntax.
- Use `from __future__ import annotations` only when technically required.
- Use timezone-aware UTC datetimes.
- Use `pathlib.Path` instead of string-based path manipulation.
- Keep network and database operations asynchronous.

### 4.4 API framework

- Use FastAPI.
- Version application endpoints under `/api/v1`.
- Keep health endpoints outside the versioned API.
- Use Pydantic models at all external boundaries.
- Keep route handlers thin.
- Place business logic in services and orchestration in graphs.
- Use FastAPI lifespan for shared resources.

### 4.5 Database

Use MongoDB through the official PyMongo asynchronous API:

```python
from pymongo import AsyncMongoClient
```

Do not use Motor. Do not introduce an ODM unless approved after a clear need is demonstrated.

Database name: `stratix`.

Initial collections:

- `organizations`
- `competitors`
- `sources`
- `scan_jobs`
- `snapshots`
- `signals`
- `insights`

Every tenant-owned document must include `organization_id`.

### 4.6 Crawling

Use Crawl4AI as the primary website collector.

Crawl4AI is a collector, not the change detector and not the strategic analyst.

Required crawler behavior:

- Crawl only public URLs registered by the user or discovered from an allowed domain.
- Check `robots.txt`.
- Use low per-domain concurrency.
- Apply delay and exponential backoff.
- Respect `Retry-After`.
- Stop automatic retries after repeated `403`, `429`, CAPTCHA, or access-denied responses.
- Store crawler name, status, final URL, fetch time, content type, and error details.
- Never attempt authentication bypass, CAPTCHA bypass, paywall bypass, or access-control evasion.
- Do not use rotating proxies in the MVP.
- Do not crawl arbitrary internal links without an explicit bounded crawl policy.

### 4.7 LangGraph

Use LangGraph as the deterministic orchestration layer.

It controls:

- State
- Node execution
- Conditional routing
- Error accumulation
- Retry boundaries
- Persistence boundaries
- Whether strategic analysis should run

It must not delegate objective change detection to an LLM.

### 4.8 Deep Agents

Use one primary Strategic Intelligence Deep Agent.

It is invoked only after verified signals exist or when a user requests strategic analysis.

The Deep Agent may delegate to specialist subagents:

- Product and Pricing Analyst
- Engineering Intelligence Analyst
- GTM and Market Analyst
- Opportunity Analyst

The agent must operate on stored, traceable signals. It must return structured output containing facts, inference, confidence, alternative explanations, recommended actions, and supporting signal IDs.

Do not create autonomous crawler agents. Crawlers and source connectors are deterministic tools/services.

---

## 5. Core domain model

### 5.1 Organization

```text
id
clerk_organization_id
name
created_at
updated_at
```

### 5.2 Competitor

```text
id
organization_id
name
canonical_url
description
market_category
status: active | paused | archived
created_at
updated_at
```

### 5.3 Source

```text
id
organization_id
competitor_id
type: website | pricing | changelog | documentation | github | news
url
enabled
crawl_interval_minutes
last_scanned_at
created_at
updated_at
```

Only website-like source types are required in the first vertical slice.

### 5.4 ScanJob

```text
id
organization_id
competitor_id
status: queued | running | succeeded | partially_succeeded | failed
started_at
completed_at
source_results
error_count
errors
created_at
```

### 5.5 Snapshot

```text
id
organization_id
competitor_id
source_id
source_url
final_url
fetched_at
http_status
crawler_used
raw_html_optional
normalized_markdown
normalized_text
content_hash
section_hashes
metadata
created_at
```

Raw HTML may be omitted or TTL-expired later. Normalized content and source evidence must remain available for traceability.

### 5.6 Signal

```text
id
organization_id
competitor_id
source_id
scan_id
type: pricing_change | feature_change | positioning_change | content_change | release | news | unknown
title
summary
observed_facts
before_excerpt
after_excerpt
changed_sections
importance_score
confidence
source_url
snapshot_before_id
snapshot_after_id
observed_at
created_at
```

A signal is an objective event record. Its `observed_facts` must be supported by stored snapshots.

### 5.7 Insight

```text
id
organization_id
competitor_id
scan_id_optional
title
summary
facts
inference
confidence
alternative_explanations
recommended_actions
supporting_signal_ids
model_provider
model_name
prompt_version
created_at
```

Facts and inference must be stored separately.

---

## 6. API requirements

All JSON endpoints return documented Pydantic response schemas.

### 6.1 Health

```http
GET /health/live
GET /health/ready
```

`/health/live` confirms the process is running.  
`/health/ready` confirms required dependencies are usable.

### 6.2 Competitors

```http
POST   /api/v1/competitors
GET    /api/v1/competitors
GET    /api/v1/competitors/{competitor_id}
PATCH  /api/v1/competitors/{competitor_id}
DELETE /api/v1/competitors/{competitor_id}
```

Deletion should be implemented as archival unless permanent deletion is explicitly requested later.

### 6.3 Sources

```http
POST   /api/v1/competitors/{competitor_id}/sources
GET    /api/v1/competitors/{competitor_id}/sources
PATCH  /api/v1/competitors/{competitor_id}/sources/{source_id}
DELETE /api/v1/competitors/{competitor_id}/sources/{source_id}
```

### 6.4 Scans

```http
POST /api/v1/competitors/{competitor_id}/scans
GET  /api/v1/scans/{scan_id}
```

The scan-creation endpoint must be idempotent when the same idempotency key is reused.

For the first local implementation, a scan may execute in-process. The service boundary must allow replacement by a durable queue without changing API contracts.

### 6.5 Signals

```http
GET /api/v1/competitors/{competitor_id}/signals
GET /api/v1/signals/{signal_id}
```

Support cursor pagination and filters for signal type and observed date.

### 6.6 Insights

```http
POST /api/v1/competitors/{competitor_id}/insights/generate
GET  /api/v1/competitors/{competitor_id}/insights
GET  /api/v1/insights/{insight_id}
```

Insight generation must fail clearly when no eligible signals exist.

### 6.7 Error format

Use one stable error envelope:

```json
{
  "error": {
    "code": "COMPETITOR_NOT_FOUND",
    "message": "The requested competitor was not found.",
    "details": {},
    "request_id": "..."
  }
}
```

Do not expose stack traces, tokens, connection strings, or raw provider errors.

---

## 7. Authentication and authorization

The Next.js frontend will send a Clerk session token:

```http
Authorization: Bearer <session-token>
```

The backend must:

1. Verify token signature and standard claims.
2. Validate the authorized party/origin configuration.
3. Read the user ID from `sub`.
4. resolve the active organization/workspace.
5. Apply `organization_id` to every query.
6. Never trust `user_id` or `organization_id` from the request body.

A development-only authentication bypass may exist only when:

```text
APP_ENV=development
AUTH_DISABLED=true
```

The application must refuse to start with authentication disabled in production.

---

## 8. Website scan workflow

### 8.1 LangGraph state

```python
class MonitoringState(TypedDict):
    organization_id: str
    competitor_id: str
    scan_id: str
    source_ids: list[str]
    collected_pages: list[dict]
    previous_snapshots: dict[str, dict | None]
    new_snapshots: list[dict]
    detected_changes: list[dict]
    signals: list[dict]
    insight: dict | None
    errors: list[dict]
    status: str
```

Use concrete typed models where practical. The state must remain serializable.

### 8.2 Nodes

```text
load_scan_context
load_sources
collect_sources
normalize_content
load_previous_snapshots
detect_changes
persist_snapshots
build_signals
persist_signals
should_generate_insight
run_strategic_agent
validate_agent_output
persist_insight
finalize_scan
```

### 8.3 Routing

```text
No enabled sources
→ fail scan with a domain error

Collection failures for every source
→ fail scan

Some sources fail, some succeed
→ continue and mark partial success

No meaningful changes
→ persist snapshots, skip strategic agent, succeed

Meaningful changes exist
→ create signals and invoke strategic agent

Agent failure
→ retain signals, mark partial success, do not discard verified data
```

### 8.4 Retries

Retries belong at external-boundary nodes, not around the whole graph.

- Retry transient network failures with bounded exponential backoff.
- Do not retry invalid URLs.
- Do not repeatedly retry explicit access denial.
- Do not retry schema-validation errors without changing input.
- Record all terminal errors in the scan job.

---

## 9. Normalization and change detection

### 9.1 Normalization

Normalization must remove unstable boilerplate where safely possible:

- navigation labels
- cookie banners
- repeated footer content
- generated timestamps that are unrelated to product content
- duplicate whitespace
- empty sections

Preserve:

- headings
- pricing values
- plan names
- feature text
- CTA labels when strategically meaningful
- structured metadata
- source URL
- fetched timestamp

### 9.2 Hashing

Create:

- full normalized-content hash
- stable per-section hashes

The same normalized content must produce the same hash.

### 9.3 Change detection

Use deterministic code to calculate:

- added sections
- removed sections
- modified sections
- before and after excerpts
- numeric pricing changes where extractable
- similarity score
- change significance

LLMs may classify or summarize an already detected change. They must not decide whether the underlying page objectively changed.

### 9.4 Noise threshold

Do not create a signal for:

- whitespace-only changes
- reordered boilerplate
- tracking parameters
- non-semantic timestamp changes
- small navigation/footer changes

Keep thresholds configurable and test them with fixtures.

---

## 10. Strategic Deep Agent

### 10.1 Invocation conditions

Invoke the Deep Agent only when:

- one or more high-value signals are created;
- several related medium-value signals accumulate;
- a user explicitly requests analysis; or
- a scheduled executive briefing is generated in a later phase.

### 10.2 Tools

The agent may receive narrowly scoped tools:

```text
get_competitor_profile
get_signals_by_ids
get_recent_signals
get_historical_signals
get_snapshot_evidence
calculate_activity_metrics
semantic_search_signals
save_strategic_insight
```

Tools must enforce organization scope internally.

### 10.3 Required output

```json
{
  "title": "Possible enterprise-market expansion",
  "summary": "The competitor appears to be increasing its enterprise focus.",
  "facts": [
    {
      "statement": "An enterprise pricing tier was added.",
      "supporting_signal_ids": ["..."]
    }
  ],
  "inference": "The combined signals suggest a shift toward enterprise acquisition.",
  "confidence": 0.82,
  "alternative_explanations": [
    "The changes may target existing customers rather than a new market."
  ],
  "recommended_actions": [
    "Compare enterprise security capabilities.",
    "Monitor sales and partnership positioning."
  ],
  "supporting_signal_ids": ["..."]
}
```

### 10.4 Guardrails

- Every factual claim must point to at least one stored signal.
- Confidence must be between 0 and 1.
- Use probabilistic language for inference.
- Include at least one alternative explanation when confidence is below 0.9.
- Reject unsupported signal IDs.
- Validate output with Pydantic before persistence.
- Store model and prompt-version metadata.
- Never let the model write directly to MongoDB without validated service functions.

---

## 11. Configuration

Use `pydantic-settings`.

Required environment variables:

```dotenv
APP_NAME=Stratix API
APP_ENV=development
APP_DEBUG=true
API_V1_PREFIX=/api/v1
LOG_LEVEL=INFO

MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=stratix

AUTH_DISABLED=true
CLERK_SECRET_KEY=
CLERK_JWKS_URL=
CLERK_AUTHORIZED_PARTIES=http://localhost:3000

CRAWLER_USER_AGENT=StratixBot/0.1
CRAWLER_MAX_CONCURRENCY_PER_DOMAIN=1
CRAWLER_MIN_DELAY_SECONDS=4
CRAWLER_MAX_DELAY_SECONDS=8
CRAWLER_MAX_RETRIES=2
CRAWLER_CHECK_ROBOTS=true
CRAWLER_PAGE_TIMEOUT_MS=45000

LLM_ENABLED=false
LLM_PROVIDER=
LLM_MODEL=
LLM_API_KEY=
LLM_BASE_URL=
STRATEGIC_PROMPT_VERSION=v1
```

No real secret may be committed.

---

## 12. Logging and observability

Use structured logs.

Every request and scan should carry a request or correlation ID.

Log:

- route and method
- status code
- duration
- organization ID when safe
- competitor ID
- scan ID
- source ID
- graph node
- crawler result
- retry count
- model provider and model name
- token usage when available
- terminal error code

Do not log:

- bearer tokens
- Clerk secret keys
- full MongoDB connection strings
- LLM API keys
- private user content
- entire crawled documents by default

---

## 13. Testing requirements

### 13.1 Unit tests

Required unit coverage:

- settings validation
- URL validation
- authorization scoping
- text normalization
- content hashing
- section extraction
- change detection
- noise suppression
- signal construction
- agent-output validation
- graph routing functions

### 13.2 Integration tests

Required integration coverage:

- health endpoints
- competitor CRUD
- source CRUD
- first scan creates a baseline snapshot without a change signal
- second identical scan creates no signal
- changed second scan creates a signal
- access to another organization’s competitor is denied
- crawler failure produces a stable API error
- agent failure does not discard stored signals
- LLM-disabled mode skips strategic analysis cleanly

All external HTTP, crawler, Clerk, MongoDB, and model interactions must be mockable.

### 13.3 Quality commands

The repository must pass:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app
uv run pytest
```

Tests must not require live API keys or external internet access.

---

## 14. Security and compliance constraints

- Validate all URLs.
- Reject non-HTTP(S) schemes.
- Protect against SSRF.
- Block loopback, link-local, private-network, and cloud-metadata destinations unless explicitly enabled for isolated local tests.
- Enforce request-body size limits.
- Apply organization scope to every repository query.
- Keep secrets in environment variables.
- Verify Clerk JWTs.
- Avoid storing unnecessary raw content.
- Respect public-source access restrictions.
- Do not implement bypass mechanisms for blocked sites.
- Sanitize model-facing content and treat crawled text as untrusted input.
- Prevent prompt-injection instructions from crawled pages from changing system behavior.
- Tool permissions must be allowlisted.

---

## 15. Performance targets

These are MVP targets, not guarantees:

- Health endpoint p95 under 100 ms locally.
- Normal CRUD endpoint p95 under 500 ms excluding network-dependent work.
- Manual scan endpoint returns a scan ID promptly rather than keeping an HTTP request open for the full crawl.
- One competitor scan supports at least 10 configured website sources.
- Per-domain concurrency defaults to 1.
- Dashboard list endpoints use pagination.
- Database indexes support organization, competitor, source, scan status, and observation time queries.

---

## 16. Implementation phases

### Phase 0 — Foundation

- Initialize uv project.
- Configure `pyproject.toml`.
- Add application factory/lifespan.
- Add settings.
- Add structured logging.
- Add health endpoints.
- Add test harness.
- Add linting and typing configuration.

### Phase 1 — Persistence and API

- Create PyMongo async client lifecycle.
- Add repositories.
- Add organization context.
- Implement competitor CRUD.
- Implement source CRUD.
- Add stable error envelope.
- Add pagination primitives.

### Phase 2 — Website monitoring vertical slice

- Add crawler abstraction.
- Add Crawl4AI implementation.
- Add crawler safety policy.
- Add normalization.
- Add deterministic hashing.
- Add snapshot persistence.
- Add manual scan endpoint.
- Add scan status endpoint.

### Phase 3 — LangGraph orchestration

- Define monitoring state.
- Implement graph nodes.
- Add conditional routing.
- Persist partial results.
- Add bounded retries.
- Add graph tests.

### Phase 4 — Signals

- Implement deterministic change detection.
- Suppress noise.
- Build and persist signals.
- Add signal APIs.
- Add fixtures for pricing and positioning changes.

### Phase 5 — Deep Agent

- Implement strategic-agent factory.
- Add allowlisted tools.
- Add specialist subagents where justified.
- Add structured output schema.
- Add evidence validation.
- Add LLM-disabled mode.
- Add mocked agent tests.

### Phase 6 — Hardening

- Add Clerk verification.
- Add SSRF protection.
- Add database indexes.
- Add Dockerfile.
- Add CI.
- Add operational README.
- Review observability and error handling.

---

## 17. Acceptance criteria

The backend MVP is accepted when all of the following are true:

1. A developer can install and run it with uv.
2. `/health/live` and `/health/ready` work.
3. A user can create a competitor and register a public website source.
4. A manual scan creates a persisted baseline snapshot.
5. Re-scanning unchanged content creates no false change signal.
6. Re-scanning changed content creates a traceable signal with before/after evidence.
7. LangGraph controls the scan flow and routes around partial failures.
8. A Deep Agent can generate a validated insight from stored signals when LLM configuration is enabled.
9. Agent facts reference valid supporting signal IDs.
10. The system still stores signals if agent generation fails.
11. Cross-organization access is blocked.
12. Crawler safeguards are enabled.
13. Tests run without live external services.
14. Ruff, mypy, and pytest pass.
15. The API is documented through OpenAPI.

---

## 18. Definition of done for each change

A change is complete only when:

- implementation matches this PRD;
- public behavior has typed schemas;
- errors use the stable envelope;
- organization scoping is preserved;
- tests cover success and important failure paths;
- format, lint, type checks, and tests pass;
- no secrets or generated environment files are committed;
- documentation is updated when setup or behavior changes;
- the final response identifies files changed, validation run, and remaining limitations.

---

## 19. Non-negotiable architecture rules

1. Crawlers collect; deterministic code detects; agents interpret.
2. Verified data must survive model failures.
3. Facts and inference must never be merged into one unqualified statement.
4. All tenant data is organization-scoped.
5. No Motor, Redis, Celery, Pinecone, social scraping, proxy rotation, or microservices in the first vertical slice.
6. No hidden fallbacks that fabricate successful crawl or AI results.
7. No live network calls in tests.
8. Do not over-engineer before the first end-to-end scan works.
