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

Enable Organizations for the Clerk application and allow users to create Organizations. Configure the application URLs for local development, including `http://localhost:3000`, `/sign-in`, `/sign-up`, and the post-auth route `/route-after-auth`. The FastAPI backend must authorize the same origin.

Users without an active Organization do not complete a separate workspace form. Company onboarding creates an Organization from the submitted company name, activates it, refreshes the default Clerk session token, and only then calls FastAPI. Existing active Organizations are reused.

## Routes

Public routes:

- `/`
- `/sign-in/**`
- `/sign-up/**`
- `/route-after-auth`

Server-protected routes:

- `/onboarding` and `/onboarding/**`
- `/dashboard` and `/dashboard/**`
- `/competitors` and `/competitors/**`
- `/settings` and `/settings/**`

Next.js 16 uses `proxy.ts` with `clerkMiddleware()` and the Clerk-recommended matcher. Sensitive route access is enforced again in the protected App Router layout with `await auth()`.

## Authenticated API behavior

`lib/api/client.ts` is the single FastAPI client. It requests the current Clerk token for each protected call, sends `Authorization`, JSON content type, and optional request ID headers, and parses the backend's stable error envelope. Tokens are never written to local storage, session storage, application databases, or logs.

The UI flow is:

```text
sign in/up
→ create or reuse active Organization
→ submit company onboarding once
→ poll initialization
→ resolve automatic/manual discovery
→ poll discovery
→ review evidence and approve/reject candidates
→ show approved competitors with monitoring pending
```

Polling is bounded and cleaned up on unmount. The UI labels relationship and confidence as assessments and does not present model output as verified fact.

## Validation

```powershell
npm run lint
npx tsc --noEmit
npm run test
npm run build
```

The lightweight offline tests use Node's built-in test runner with TypeScript transformation; they cover bearer-token headers, missing-token handling, stable backend errors, active-Organization reuse, and Organization creation/activation order. Browser-level Clerk redirects and live sign-in are not automated in this repository.
