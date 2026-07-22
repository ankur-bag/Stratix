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

## Validation

```powershell
npm run lint
npx tsc --noEmit
npm run test
npm run build
```

The lightweight offline tests use Node's built-in test runner with TypeScript transformation; they cover Organization-scoped bearer tokens, missing Organization/token handling, stale-response rejection, cancellation, company-field validation, activation settling, and centralized workspace routing. Browser-level Clerk redirects and live sign-in are not automated in this repository.

Manual browser smoke tests remain required for creating two companies, switching repeatedly while requests poll, verifying each company lands on its own onboarding/dashboard state, reloading after a switch, and confirming no previous-company data flashes. Also verify baseline polling, source-domain and robots warnings, evidence links, and narrow screens. Do not run a live competitor crawl without authorization.
