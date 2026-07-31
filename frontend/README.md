# Stratix Frontend

The Stratix Next.js application provides Clerk sign-in, Organization-scoped company onboarding, initialization progress, evidence-based competitor review, and the approved-competitor dashboard.

## Setup

This project uses Next.js 16 App Router, npm, React 19, Tailwind CSS 4, and `@clerk/nextjs`.

The Clerk CLI was not available during Phase 3 implementation, so integration follows Clerk's official Next.js quickstart manually. To bind the repository interactively to the intended Clerk application, run from `frontend/`:

```powershell
clerk auth login
clerk init --app app_3Go2V4t4vdMRxRb0IuvsYZYbiOq
clerk doctor
```

Do not rerun initialization over a working Clerk setup. Copy the safe example and add your own local keys:

```powershell
Copy-Item .env.example .env.local
npm install
npm run dev
```

```dotenv
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_placeholder
CLERK_SECRET_KEY=sk_test_placeholder
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` is browser-visible. `CLERK_SECRET_KEY` is server-only and must never be referenced from a Client Component. Never commit `.env.local`, credentials, or session tokens.

## Clerk Dashboard configuration

Enable Organizations for the Clerk application and allow users to create Organizations. Configure the application URLs for local development, including `http://localhost:3000` and `http://localhost:3002`, `/sign-in`, `/sign-up`, and the post-auth route `/route-after-auth`. The FastAPI backend must authorize the same origin.

Keep Clerk in single-session mode. Enable Organizations and user-created Organizations, and do not enable personal workspaces for Stratix navigation. The Organization switcher uses `/companies/new` for creation and `/route-after-auth` after selection. Add the exact local origins used by the frontend (normally `http://localhost:3002`, optionally port 3000 as well) to Clerk's allowed origins/redirect configuration and to the backend authorized parties.

Each Clerk Organization is exactly one Stratix company. `/companies/new` creates and activates an Organization before onboarding; onboarding never creates an Organization itself. `/companies` lists the signed-in user's memberships without aggregating backend tenant data.

## Routes

Public routes:

- `/`
- `/sign-in/**`
- `/sign-up/**`
- `/route-after-auth`

Server-protected routes:

- `/onboarding` and `/onboarding/**`
- `/companies` and `/companies/new`
- `/dashboard` and `/dashboard/**`
- `/competitors` and `/competitors/**`
- `/intelligence`
- `/strategy`
- `/settings` and `/settings/**`

Next.js 16 uses `proxy.ts` with `clerkMiddleware()` and the Clerk-recommended matcher. Sensitive route access is enforced again in the protected App Router layout with `await auth()`.

## Authenticated API behavior

`lib/api/client.ts` is the single FastAPI client. Every protected call requires an active Organization, requests a fresh Clerk token explicitly for that Organization ID, sends `Authorization` and optional request ID headers, and parses the backend's stable error envelope. A response is rejected if the active Organization changes while it is in flight. Components abort requests and clear polling through their unmount cleanup. Tokens are never written to browser storage, application databases, or logs.

The UI flow is:

```text
sign in/up
→ create or reuse active Organization
→ submit company onboarding once
→ poll initialization
→ resolve automatic/manual discovery
→ poll discovery
→ review evidence and approve/reject candidates
→ open an approved competitor workspace
→ discover or manage bounded public sources
→ establish the first-scan baseline
→ run a later manual rescan
→ review objective before/after signals
```

Polling is bounded and cleaned up on unmount. The UI labels relationship and confidence as assessments and does not present model output as verified fact.

## Phase 4 competitor intelligence

`/competitors/[competitorId]` displays the approved competitor, monitoring and baseline state, enabled public sources, recent scan progress, and recent evidence-linked signals. Users can add or disable a same-domain source, rediscover sources, create a baseline, run a later scan, and review or dismiss signal records. Active scans disable conflicting controls and poll at a bounded interval with cleanup on unmount.

The initial scan establishes evidence and intentionally shows no change signal. A successful rescan renders either “No meaningful changes detected” or “Changes detected”; partial and failed scans remain distinct. Signal cards use “Observed change,” “Evidence,” “Before,” “After,” and “Detected on” language because Phase 4 signals are objective observations, not strategic interpretations.

The authenticated client in `lib/api/client.ts` owns every monitoring request, asks Clerk for a token for each call, encodes resource IDs, generates idempotency keys for scan/discovery writes, and parses the backend error envelope. Tokens are never stored in browser storage.

## Private company knowledge

Onboarding accepts one optional private PDF (20 MB and 100 pages maximum). The browser obtains an opaque upload target from FastAPI, uploads the PDF, verifies completion with SHA-256, and includes only the resulting `document_id` in onboarding. Upload can be cancelled and failures remain retryable without exposing storage, embedding, index, namespace, or provider credentials.

Settings includes the Organization-scoped Company knowledge section with filename, upload date, size, pages, processing stage, progress, chunks, user-safe warnings, rebuild, and delete actions. Switching the active Clerk Organization aborts in-flight reads and clears document state through the existing Organization-bound API client. Scanned/image-only and password-protected PDFs are not supported yet.

For local verification, start the backend, run `npm run dev`, sign in through
Clerk, select an Organization, and exercise onboarding upload and Settings
document management. Use only authorized test PDFs. Provider credentials and
the Pinecone host belong exclusively in `backend/.env`; they must never be
added to browser-exposed configuration or a `NEXT_PUBLIC_*` variable.

## Validation

```powershell
npm run lint
npx tsc --noEmit
npm run test
npm run build
```

The lightweight offline tests use Node's built-in test runner with TypeScript transformation; they cover Organization-scoped bearer tokens, missing Organization/token handling, stale-response rejection, cancellation, company-field validation, activation settling, and centralized workspace routing. Browser-level Clerk redirects and live sign-in are not automated in this repository.

Manual browser smoke tests remain required for creating two companies, switching repeatedly while requests poll, verifying each company lands on its own onboarding/dashboard state, reloading after a switch, and confirming no previous-company data flashes. Also verify baseline polling, source-domain and robots warnings, evidence links, and narrow screens. Do not run a live competitor crawl without authorization.

## Phase 5 monitoring and notifications

An approved competitor workspace now includes an Organization-scoped monitoring schedule. Users can choose Manual only, Daily, Every 3 days, or Weekly; pause/enable automatic monitoring; inspect the next run plus last success/failure; and select **Run now**. Run now returns an accepted domain scan and durable-job summary. The confirmation explicitly says the work is safely queued, so leaving or refreshing the page does not imply cancellation.

Queued/running progress continues through the domain-specific scan endpoint. Polling is bounded, aborts on unmount, and ignores stale responses after a Clerk Organization change. Automatic schedules and manual scans share the same source-safety checks and active competitor lock.

Authenticated navigation includes one notification control with an unread badge, recent bounded list, loading/empty/error states, mark-one-read, and mark-all-read. It polls every 45 seconds only while the document is visible. Hiding the tab aborts the active request and clears the timer; returning to the tab refreshes immediately. The control is keyed by the active Clerk Organization, so a switch unmounts the old state before the new Organization is fetched and no prior-company notification flash is retained.

The browser never submits an Organization ID or calls crawlers, Gemini, Pinecone, storage, or other providers directly. All monitoring and notification requests flow through `lib/api/client.ts`, acquire a fresh active-Organization Clerk token, abort cleanly, and reject responses that arrive after an Organization switch.

At least one backend worker process is required to execute queued work. At least one backend scheduler process is required for automatic monitoring. The FastAPI API does not run long-running jobs directly in production.

## Phase 6 intelligence workspace

`/intelligence` is the Organization-scoped executive evidence workspace. It loads indexing
health, recent canonical changes, deterministic category trends, and cross-source
correlations. Users can search in ranked-evidence mode or build a deterministic,
evidence-cited answer; competitor, date-range, category, source, and optional company
context filters are sent only through the authenticated FastAPI client.

Every search is abortable. Changing a filter or mode cancels the active request and clears
its results, unmounting aborts all workspace requests, and a Clerk Organization switch
remounts the workspace with empty state before loading the new Organization. Evidence
rows show bounded excerpts, dates, categories, source labels, and public source links.
The UI never receives full crawled pages, PDF bodies, vectors, prompts, namespaces, or
provider credentials.

The reindex action queues one durable Organization job and leaves the currently active
evidence searchable while the new projection is prepared. Indexing warnings describe a
recoverable workspace state without implying that the underlying competitor scan failed.
Competitor detail pages also expose a paginated canonical evidence timeline with source
dates and signal before/after excerpts. Phase 6 provides evidence retrieval and
historical intelligence; strategic recommendations and autonomous Deep Agent reasoning
remain reserved for Phase 7.

## Phase 7 strategy workspace

`/strategy` turns already-collected, Organization-authorized evidence into a reviewed
decision brief. Ask mode accepts a focused strategic question, Compare mode requires at
least two approved competitors, and Executive brief mode accepts an optional focus.
Users can bound the date range and select up to ten competitors. Submitting creates a
durable backend run; it does not call Gemini, Pinecone, a crawler, or any other provider
from the browser.

The workspace displays queued, retrieval, analysis, validation, partial, successful,
failed, cancelled, and retry states. Its single visibility-aware poller pauses while the
tab is hidden and all reads are aborted on unmount or Clerk Organization change. Active
runs can be cancelled, failed runs can be retried, and a completed report can be rerun
against the latest evidence over the same-sized lookback window.

Reports visibly separate verified observations, supported inferences, competitor
comparisons, threats, opportunities, and advisory recommendations. Recommendations show
impact, effort, horizon, confidence, assumptions, risks, and success indicators.
Uncertainties and evidence gaps are first-class sections. Citation controls retrieve a
fresh bounded excerpt from FastAPI; deleted or deauthorized evidence produces a safe
unavailable state rather than displaying stale browser data.

Report history and feedback are Organization-scoped. Switching Organizations remounts
the complete workspace and clears reports, citations, errors, feedback, polling, and
in-flight responses before the next workspace loads. Provider failures, timeouts,
insufficient evidence, partial reports, and deterministic fallback are user-visible
without exposing provider exceptions. A fallback is labeled as a verified evidence
summary, never as an AI strategic report.

The Strategic Deep Agent does not crawl websites directly. It operates only on evidence
already collected, authorized, and indexed by Stratix. Recommendations are advisory and
are never executed automatically.
