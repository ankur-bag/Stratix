# Stratix Final Acceptance Report

Acceptance run: 2026-08-02 (Asia/Calcutta)

Status vocabulary: `PASS`, `FAIL`, `BLOCKED`, `NOT RUN`, `EXPECTED EXTERNAL LIMITATION`.

This report distinguishes live integration evidence from controlled/offline regression evidence. A controlled or fake-provider result is never used as a substitute for a blocked live provider path.

## 1. Environment

| Item | Result |
|---|---|
| Host | Windows NT 10.0.26200.0 |
| PowerShell | 5.1.26100.8457 |
| Python | 3.12.13 |
| uv | 0.11.19 |
| Node.js | 24.14.1 |
| npm | 11.11.0 |
| Frontend | Next.js 16.2.10, development server on `127.0.0.1:3000` |
| Backend | FastAPI/Uvicorn on `127.0.0.1:8000` |

No environment values, credentials, bearer tokens, PDF contents, model prompts, or vectors are included in this report.

## 2. Git revision and dirty-state summary

- Revision: `8c2f5fc5a39b1cf9ef11b24de830b6c8512fb48e`
- Branch: `main`
- Initial `git status --short`: clean.
- Initial `git diff --stat`: empty.
- Initial `git diff --check`: clean.
- Sensitive tracked-file scan: `PASS`; no tracked real `.env`, `.env.local`, PDF upload, or `backend/.data` artifact was found.
- The only repository change produced by this run is this requested report.

## 3. Services started

| Service | Status | Evidence |
|---|---|---|
| API | `PASS` | Started under Uvicorn; `/health/live`, `/health/ready`, and `/openapi.json` returned 200. |
| Worker | `PASS` | Separate supervised process; diagnostic reported `worker_available=true`. |
| Scheduler | `PASS` | Separate supervised process; diagnostic reported `scheduler_available=true`. |
| Frontend | `PASS` | One verified Stratix Next.js process chain served `/` with 200. |
| Startup stack trace | `PASS` for Uvicorn launch | The documented `fastapi dev` launcher failed in this non-UTF-8 console while printing its own emoji. Uvicorn started the same app without a startup exception. |

The four process streams were supervised separately. The runner did not persist usable log files, so the complete log-content audit is recorded as `NOT RUN`, not passed.

## 4. Provider configuration status

| Provider/capability | Status | Safe diagnostic result |
|---|---|---|
| MongoDB | `PASS` | Readiness was `ready`; worker/scheduler diagnostics queried the live persistence layer successfully. |
| Clerk backend verification | `PASS` (configuration only) | Server secret configured; two authorized parties configured; no JWT public key configured. |
| Clerk browser session | `BLOCKED` | Browser control failed before opening a tab because the Node REPL MCP request lacked required sandbox metadata. |
| Crawl4AI | `PASS` | Real bounded crawl executed against public Avento. |
| Tavily | `BLOCKED` | `tavily_configured=false`. |
| Gemini key presence | `PASS` (presence only) | A server-side key is configured; its value was not read or printed. |
| Gemini embeddings | `BLOCKED` | `embeddings_enabled=false`; readiness reported embeddings unavailable. |
| Pinecone | `BLOCKED` | API key presence was reported by application settings, but host is not configured and vector search is disabled. Readiness reported vector store unavailable. |
| Strategic Gemini Agent | `BLOCKED` | `enabled=false`, `configured=false`, model not configured. |
| Object storage | `PASS` (configuration only) | Local storage provider selected; authenticated upload was not run. |

Configured vector dimensions are 768 and configured metric is cosine. These settings do not establish a live Pinecone connection.

Diagnostic caveat: the requested command `scripts/check_pinecone.py validate` is not supported by the current CLI. Running the script with its actual interface did not load the application's dotenv configuration and exited with a missing-key message. The runtime readiness result and application setting flags were therefore used to establish that Pinecone is unavailable; no index validation was claimed.

## 5. Automated regression results

| Command | Status | Duration | Result |
|---|---|---:|---|
| `ruff format --check app tests scripts` | `PASS` | 1.40 s | 137 files already formatted |
| `ruff check app tests scripts` | `PASS` | 0.07 s | All checks passed |
| `mypy app` | `PASS` | 17.23 s | 81 files checked, 0 issues |
| Full backend pytest suite | `PASS` | 24.92 s wrapper / 12.12 s pytest | 192 passed |
| Frontend lint | `PASS` | 83.57 s | No lint failures |
| Frontend TypeScript | `PASS` | 6.26 s | No type errors |
| Frontend tests | `PASS` | 1.02 s | 50 passed |
| Frontend production build | `PASS` | 79.67 s | Build and all 15 routes completed |

Warnings:

- Starlette TestClient/httpx deprecation warning recommending `httpx2`.
- Node experimental type-transform warning.
- Node module-type warning caused test files to be reparsed as ES modules.

Focused controlled suites also passed:

- 79 tests covering crawler policy, PDF validation, tenancy boundaries, leases, retries, cancellation, scheduler behavior, citation enforcement, prompt boundaries, and budgets.
- 21 tests covering change fixtures, scanning, schedules, intelligence synthesis, and Strategic Agent job flows.
- Offline strategic evaluation: 12/12 scenarios passed, 100% allowed-citation validity, 0 cross-tenant leaks, 0 prompt-injection violations. This was schema/fixture evaluation, not a live Gemini run.

## 6. Live authentication results

| Test | Status | Evidence |
|---|---|---|
| Unauthenticated protected frontend routes | `PASS` | `/onboarding`, `/companies`, `/competitors`, `/intelligence`, `/strategy`, and `/settings` returned 307 to `/sign-in`. |
| Sign-in page availability | `PASS` | `/sign-in` returned 200. |
| Signed-in route loading | `BLOCKED` | Browser automation could not open the in-app browser. |
| Signed-in auth-action gating | `BLOCKED` live / `PASS` static | Source renders sign-in/up only in the signed-out branch and OrganizationSwitcher/UserButton only in the signed-in branch. Auth pages server-redirect authenticated users. |
| Backend request without bearer token | `PASS` | 401, `AUTHENTICATION_REQUIRED`, safe message, request ID present. |
| Backend malformed bearer token | `PASS` | 401, `INVALID_SESSION_TOKEN`, safe message, request ID present. |
| Expired bearer token | `NOT RUN` | No safe reproducible expired Clerk token was available. |
| Tenancy from active Clerk Organization | `BLOCKED` | Requires a signed-in browser session and active organization. |

## 7. Organization-isolation results

Live Organization A/B creation, switching, stale-request observation, and forged-resource API requests are `BLOCKED` because a real Clerk session could not be established through the supported browser channel.

Controlled regression coverage is `PASS` for:

- organization-scoped client requests with no client-controlled `organization_id`;
- rejecting a response when the active organization changes mid-request;
- organization-keyed notification state;
- cross-tenant job deduplication boundaries;
- foreign strategy run/citation rejection;
- MongoDB reauthorization of intelligence evidence.

These controlled results do not establish the required live result. Live cross-tenant leakage count is therefore not reported as zero.

## 8. Avento crawl results

Public reachability:

| URL | Status | Time |
|---|---:|---:|
| `https://avento-ai.vercel.app/` | 200 | 0.932 s |
| `https://www.intercom.com/` | 200 | 0.511 s |
| `https://botpress.com/` | 200 | 1.715 s |
| `https://www.voiceflow.com/` | 200 | 0.157 s |

Authenticated onboarding through the normal API is `BLOCKED`. A supplemental real Crawl4AI adapter run was executed with robots enforcement, concurrency 1, depth 1, maximum 4 pages, zero retries, and 45-second page timeout.

| Metric | Observed |
|---|---:|
| Crawl status | `FAIL` against full acceptance expectation |
| Duration | 31,273.32 ms |
| Links discovered | 4 |
| Same-domain links accepted | 3 |
| External links rejected | 1 |
| Pages successful | 3 |
| Pages failed | 0 |

Pages (no scraped body content is included):

| URL | HTTP | Visible characters | Links | Crawl time |
|---|---:|---:|---:|---:|
| `https://avento-ai.vercel.app` | 200 | 7,100 | 3 | 8,334.60 ms |
| `https://avento-ai.vercel.app/sign-in` | 200 | 191 | 2 | 2,680.80 ms |
| `https://avento-ai.vercel.app/sign-up` | 200 | 243 | 2 | 1,658.43 ms |

Defect: account/login routes were crawled even though the acceptance policy requires login/account/noise routes to be excluded. Sitemap use, page classification, authenticated snapshot persistence, duplicate suppression, and partial-failure behavior were not established by this supplemental crawl.

Crawl safety policy test: `PASS` at the application-policy layer. All seven prohibited inputs (loopback, localhost, private ranges, link-local metadata address, `file:`, and `ftp:`) were rejected before crawler access with stable `INVALID_CRAWL_TARGET` messages. The same checks through an authenticated normal API request are `BLOCKED`.

## 9. PDF ingestion results

Synthetic artifact generation and local validation: `PASS`.

- Three pages generated; 3,897 observed bytes.
- SHA-256: `7c80862ecaa1c74097ad04d75db1ee9216b3bd0fc14cfa2dbbf3eccbcfcf6773`.
- Three page renders visually inspected: no clipping, overlap, or unreadable text.
- Product extractor returned 3 pages, 541 normalized characters, no warnings, and preserved the security marker as ordinary extracted text.
- Deterministic chunker produced 3 bounded chunks; text hashes and synthetic vector IDs were identical across two runs; page spans were retained.

Live upload validation, MongoDB ownership/version activation, Gemini embeddings, Pinecone upsert, retrieval, replacement failure handling, and deletion cleanup are `BLOCKED` by Clerk and disabled/unavailable vector services.

The focused controlled suite passed invalid-extension/MIME, declared/streamed size, invalid/corrupt/encrypted/image-only/no-text/page-limit, cleanup, idempotency, deterministic chunking, version activation, and authorization cases. Those results are not reported as live ingestion.

## 10. Competitor discovery results

`BLOCKED` - Tavily is not configured and the authenticated company workspace was unavailable. No discovery request, candidate approval, or foreign-candidate live test was performed.

## 11. Baseline and rescan results

`BLOCKED` - no live competitors could be approved in a QA Organization. No competitor snapshot, semantic indexing job, Pinecone count change, or no-change rescan was created.

## 12. Change-detection results

Required controlled positive fixture: `FAIL`.

The detector marked the fixture meaningful with score 0.8, but only 2 of 5 required semantics were correctly identified:

| Required detection | Result |
|---|---|
| Free-tier removal | `FAIL` - not detected |
| Starter price addition | `FAIL` - misclassified as a decrease from 49 to 19 |
| Pro price increase 49 to 59 | `FAIL` - 59 was treated as a newly added positional price |
| Business plan addition | `PASS` |
| Voice feature addition | `PASS` |

Root behavioral issue: prices are compared by global positional order instead of plan/section identity, and the phrase form `Starter plan - Free` is not recognized as a free plan/tier.

Noise-only fixture: `PASS`; meaningful signals = 0. Cookie/footer/navigation changes and punctuation/whitespace differences were ignored safely.

Notification-once and retry idempotency for this exact five-signal fixture are `NOT RUN` because the required detections failed before those assertions could be meaningful.

## 13. Worker and scheduler results

| Test | Status | Evidence |
|---|---|---|
| Worker heartbeat | `PASS` | Fresh heartbeat observed. |
| Scheduler heartbeat | `PASS` | Fresh heartbeat observed. |
| Queue state | `PASS` | 0 queued jobs, no oldest age. |
| Lease state | `PASS` | 0 expired leases. |
| Dead letters | `PASS` | 0 dead-letter jobs. |
| Live worker claim | `BLOCKED` | No safe QA job could be created without Clerk. |
| Live two-scheduler occurrence dedupe | `NOT RUN` | Starting a second scheduler against existing non-QA schedules would be unsafe. |
| Controlled scheduler dedupe | `PASS` | Two schedulers, deterministic occurrence keys, tenant separation, and 1,000 unique schedule occurrences passed automated tests. |
| Controlled backpressure | `PASS` | Deferred by 15 minutes with degraded reason retained. |

## 14. Crash-recovery results

Live API-only queueing, worker termination/lease expiry/reclaim, lost-lease ownership, cancellation at each handler stage, retry/dead-letter transitions, manual/scheduled collision, and strategic worker recovery are `NOT RUN` or `BLOCKED` because no dedicated QA domain resources could be created.

Controlled recovery tests are `PASS` for expired-lease recovery, attempt increments, dead lettering, no worker sleep during backoff, permanent failure, lost-lease result discard, graceful shutdown, bounded global concurrency, and queued cancellation. These are not substituted for the live crash test.

## 15. Intelligence search results

Live indexing/search/synthesis/reindex is `BLOCKED` because embeddings and vector search are disabled and Pinecone has no configured host. Live Organization authorization filters, canonical evidence links, and synthesized citations were not exercised.

Controlled search/security/synthesis tests are `PASS`, including bounded excerpts, canonical reauthorization, stale/deleted/inactive/foreign evidence rejection, unknown citation fallback, and provider-failure fallback.

## 16. Strategic Agent results

Live ask, compare, executive-brief, history/rerun, provider recovery, and cancellation are `BLOCKED` because the Strategic Agent is disabled and no model is configured.

Static/controlled preflight is `PASS` for:

- provider implementation is Google GenAI;
- application source has no Anthropic/Claude integration;
- eight approved read-only evidence tools;
- filesystem and execute tools disabled (`ls`, read/write/edit file, glob, grep, execute);
- generic general-purpose subagent disabled;
- maximum 4 specialists, 24 total tool calls, 12 iterations, and 240-second timeout;
- no model call from health checks.

## 17. Citation audit

Opening and auditing every citation in a live report is `BLOCKED` because no live report exists.

Offline fixed-scenario evaluation: `PASS`; 12/12 scenarios, 100% allowed-citation validity, one deliberately unsupported citation rejected, one missing-citation payload rejected, and cross-tenant leakage count 0 in the controlled fixture. This is not the required live citation audit.

## 18. Prompt-injection results

Live Gemini prompt-injection execution is `BLOCKED`.

Controlled evidence-boundary checks are `PASS`:

- the synthetic PDF security marker was extracted as plain text;
- system prompt explicitly labels evidence untrusted and forbids executing its instructions;
- model-controlled tenant/tool overrides are rejected;
- unknown/foreign citations are rejected;
- offline malicious-evidence scenario reported 0 prompt-injection violations;
- no shell, filesystem, or network tools are exposed to the strategic harness.

No claim is made that a real Gemini response resisted the injection.

## 19. Browser/UI results

Supported browser automation: `BLOCKED`. The browser skill's required Node REPL tool failed with `codex/sandbox-state-meta: missing field sandboxPolicy` before a tab could open. No alternative credential bypass was used.

Safe HTTP/static findings:

- `/sign-in` returned 200; major protected routes redirected to sign-in.
- Literal `/company-knowledge` and `/notifications` probes returned 404; the repository implements these as embedded components rather than standalone pages. Their intended signed-in workflows are `NOT RUN`.
- Global CSS assigns pointer cursor to enabled links, buttons, summaries, selects, file inputs, checkboxes, radios, and enabled button roles; disabled controls use `not-allowed`.
- Frontend tests cover abort/stale-organization handling, organization-keyed state, hidden-tab polling pause, notification dedupe, and strategy terminal polling.

Desktop/mobile layout, overflow, focus, Escape behavior, modal behavior, organization-switch flashes, browser console, network/CORS loops, and screenshots are `BLOCKED`.

## 20. Security and log audit

| Check | Status | Evidence |
|---|---|---|
| Tracked AI/vector public secrets | `PASS` | No `NEXT_PUBLIC_GEMINI` or `NEXT_PUBLIC_PINECONE` tracked matches. |
| Tracked server secret values | `PASS` | Only blank examples, documented placeholders, and setting names were found. |
| Client static bundle | `PASS` | No Gemini/Pinecone key names, MongoDB URI, `mongodb+srv`, or `sk_live_`/`sk_test_` values in `.next/static`. Clerk's library includes the literal server-key setting name, but no secret value/prefix. |
| Direct browser provider calls | `PASS` static | No Gemini or Pinecone endpoint references in frontend application source; API base points to FastAPI. |
| Auth error envelope | `PASS` | Both tested 401 responses contained stable code, safe message, and request ID; no stack trace. |
| Other representative live error envelopes | `BLOCKED` | Require authenticated resources/providers. |
| Complete API/worker/scheduler log audit | `NOT RUN` | Separate streams were supervised but no usable persisted log capture was produced. |

No application secret was intentionally printed or included in the report.

## 21. Performance observations

Live health probe: `PASS`; 20/20 responses succeeded, p50 12.14 ms, p95 17.93 ms, maximum 374.99 ms.

Offline controlled benchmarks (fake providers/in-memory persistence, not live service SLAs):

| Benchmark | Result |
|---|---|
| Job queue | 10,000 inserted; 1,000 claims; 100 recoveries; 1,000 schedules; 5,000 notifications in 7.6604 s |
| Document sizing | 100 pages; 1,099 chunks; 35 fake embedding batches; 11 fake vector batches; 4.0304 s; 31.82 MB peak |
| Intelligence search | 1,000 evidence records; 100 searches; p50 270.360 ms; p95 340.996 ms; max 439.210 ms; 29.006 MiB peak |
| Strategic schema | 1,000 valid reports and 1,000 invalid payload rejections in 0.0233 s |

Live bounded provider concurrency, multi-job API responsiveness, and organization fairness are `BLOCKED`.

## 22. Cleanup performed

- Synthetic PDF and all three rendered PNGs deleted: `PASS`.
- Pytest scratch directories deleted: `PASS`.
- API, worker, scheduler, and Stratix frontend stopped: `PASS`.
- Listener verification: 0 listeners on ports 8000 and 3000.
- QA Organizations: none created, so none removed.
- QA database/Pinecone records: none created, so no domain cleanup was required.
- Existing non-test user data was not modified or deleted.

## 23. Failures

1. `FAIL` - live Crawl4AI traversal followed Avento `/sign-in` and `/sign-up`, violating the required login/account/noise exclusion.
2. `FAIL` - the exact positive pricing fixture missed free-tier removal, starter price addition, and Pro price increase because price comparison is positional and free-tier phrase recognition is too narrow.
3. `FAIL` - documented Pinecone diagnostic invocation (`validate`) is incompatible with the current script CLI; the script also does not load application dotenv settings when run directly.

## 24. Blocked tests

Critical blocked areas:

- real Clerk sign-in and signed-in route/browser audit;
- creation and switching of the two QA Organizations;
- live cross-Organization isolation and forged-resource matrix;
- authenticated Avento onboarding, persistence, idempotency, duplicate suppression, and partial-failure flow;
- live PDF upload, Gemini embeddings, Pinecone upsert/search, replacement, retrieval, and deletion;
- Tavily discovery and competitor approval/baselines/rescans;
- live worker crash recovery and scheduler deduplication;
- live intelligence retrieval/synthesis/reindex;
- live Strategic Agent ask/compare/brief, citations, injection, recovery, and cancellation;
- full browser console/network/responsive audit;
- final two-Organization database consistency query.

Primary blockers are the browser automation platform error, absent Tavily configuration, disabled embeddings/vector search, missing Pinecone host, and disabled/unconfigured Strategic Agent.

## 25. Remaining risks

- Live tenant isolation has not been demonstrated with real Clerk Organizations.
- Provider-backed knowledge and intelligence paths could still fail despite fake-provider regression coverage.
- Account-route crawling can store low-value or auth-shell evidence and consume crawl limits.
- Positional price matching can produce materially wrong pricing signals and downstream strategy conclusions.
- Full service logs were not captured, so identifier completeness and runtime log redaction are not established.
- UI behavior across target viewports and live organization changes remains unobserved.
- No live crash, cancellation, backpressure, or concurrent scheduler collision was executed against MongoDB.

## 26. Final verdict

# NOT READY - FIXES REQUIRED

All automated regression gates passed, and the real bounded Avento crawl proved public Crawl4AI connectivity. However, two required behaviors failed (account-route exclusion and plan-aware pricing change detection), while multiple critical live acceptance paths are blocked by browser and provider configuration. Stratix must not be described as fully validated or ready for demo/control beta from this run.

Minimum retest conditions:

1. Exclude login/account/noise paths before enqueueing crawl pages and rerun the bounded Avento crawl.
2. Match pricing changes by stable plan/section identity and recognize the required free-plan wording; rerun the exact five-change fixture.
3. Restore the supported browser automation channel and complete real Clerk Organization A/B flows.
4. Configure Tavily, enable Gemini embeddings, configure Pinecone host/index, enable the Strategic Agent with its Gemini model, and rerun all provider-backed live paths.
5. Persist sanitized separate service logs and complete the live recovery, scheduler, citation, injection, UI, and consistency audits.
