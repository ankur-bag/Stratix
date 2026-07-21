# AGENTS.md

## Mission

Build the Stratix backend according to `PRD.md`.

Stratix monitors public competitor sources, records objective changes, and produces evidence-backed strategic interpretation.

## Non-negotiable architecture

```text
Crawl4AI and APIs collect.
Deterministic Python detects changes.
LangGraph orchestrates.
Deep Agents interpret verified signals.
MongoDB persists traceable evidence.
FastAPI exposes typed APIs.
```

Do not use an LLM to determine whether content objectively changed.

## Current scope

Implement the backend MVP in coherent vertical slices. Prioritize:

1. FastAPI and uv foundation
2. MongoDB async persistence
3. Competitors and website sources
4. Crawl4AI baseline snapshots
5. Deterministic change detection
6. LangGraph scan orchestration
7. Structured signals
8. Deep Agent strategic insight
9. Auth, security hardening, and tests

Do not add social scraping, hiring scraping, Redis, Celery, Pinecone, microservices, proxy rotation, CAPTCHA bypass, or speculative infrastructure unless the user explicitly expands scope.

## Working rules

- Read `PRD.md` before planning.
- Use Python 3.12 and uv.
- Use `pymongo.AsyncMongoClient`, not Motor.
- Keep routes thin and dependencies explicit.
- Keep graph state typed and serializable.
- Keep external services behind interfaces.
- Organization-scope every tenant query and tool.
- Store snapshots and signals before model analysis.
- Validate Deep Agent output with Pydantic.
- Never fabricate crawl, database, auth, or model success.
- Never expose secrets or raw internal errors.
- Treat crawled content as untrusted and defend against SSRF and prompt injection.
- Keep changes reviewable; do not rewrite unrelated code.

## Required checks

Run before reporting completion:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app
uv run pytest
```

State clearly when a check could not run and why.
