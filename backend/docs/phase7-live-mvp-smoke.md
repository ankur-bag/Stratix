# Phase 7 authorized live MVP smoke test

This procedure is intentionally manual. The offline suite does not prove live Clerk,
MongoDB Atlas, Crawl4AI, Tavily, Gemini, Pinecone, or object-storage connectivity. Run it
only with an authorized non-production Organization, public competitor sites you are
allowed to collect, and a non-sensitive test PDF.

## Environment and processes

Configure MongoDB Atlas, Clerk, Crawl4AI, Tavily, Gemini, Pinecone, local or private S3
document storage, durable-worker settings, scheduler settings, evidence indexing, and
the Phase 7 strategic-agent settings. Keep all credentials in the ignored backend
`.env`; never add them to frontend `NEXT_PUBLIC_*` variables.

Start four separate terminals:

```powershell
# backend terminal 1
uv run fastapi dev app/main.py

# backend terminal 2
uv run python -m app.worker

# backend terminal 3
uv run python -m app.scheduler

# frontend terminal
npm run dev
```

The worker is required for PDF processing, competitor scanning, evidence indexing, and
strategic analysis. The scheduler is required only for scheduled monitoring.

## Complete product flow

1. Sign in with Clerk and create or select the Avento Organization.
2. Complete company onboarding and upload an authorized Avento test PDF.
3. Confirm PDF processing and company website collection complete.
4. Confirm the company intelligence profile becomes ready.
5. Discover candidates, approve at least two competitors, and wait for baselines.
6. Confirm monitoring schedules exist and run a later manual scan.
7. Confirm snapshots and objective deterministic signals are created.
8. Confirm all evidence-indexing jobs complete and the Pinecone record count increases
   only in Avento's derived Organization namespace.
9. Search `/intelligence`; inspect excerpts, dates, competitors, and citations.
10. Open `/strategy` and ask what Avento should prioritize from recent competitor
    activity.
11. Confirm an accepted durable run appears, the worker advances its stages, and no
    long-running analysis executes inside FastAPI.
12. Confirm relevant specialists are counted and the report separates observations,
    inferences, and advisory recommendations.
13. Open every citation and verify the current canonical excerpt supports the claim.
14. Confirm no unsupported competitor fact, hidden reasoning, provider exception, raw
    PDF body, vector, namespace, prompt, or credential appears.
15. Generate a two-competitor comparison and a 90-day executive brief.
16. Confirm report history persists, rerun works, and feedback can be updated.
17. Switch to a second Clerk Organization. Confirm Avento documents, intelligence,
    active runs, reports, citations, notifications, and feedback disappear immediately.
18. Add different authorized evidence, confirm a distinct Pinecone namespace, and run a
    second strategic analysis.
19. Confirm no Avento evidence or report content appears, then switch back and confirm
    Avento history returns.

## Recovery

1. Start a strategic analysis and stop the worker during execution.
2. Wait longer than the configured job lease, restart the worker, and confirm the job
   recovers or retries with the same strategy run ID.
3. Confirm exactly one final report and one terminal notification exist.

## Cancellation

1. Start another analysis and cancel it while it is running.
2. Confirm the worker observes cancellation, discards any later model response, persists
   no successful report, and presents a safe cancelled state.

## Failure and retry

1. In the authorized test environment, temporarily remove provider access or configure a
   controlled timeout.
2. Start an analysis and confirm retry scheduling followed by a user-safe failure or
   deterministic fallback.
3. Restore configuration, retry the failed run, and confirm successful completion
   without a duplicate report or notification.

Record actual results. Do not describe the live Gemini integration or the complete MVP
flow as successful unless every applicable step was executed and verified.
