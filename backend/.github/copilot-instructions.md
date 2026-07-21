# GitHub Copilot Repository Instructions

## Project context

This repository contains the Python backend for **Stratix**, an AI strategic-intelligence platform. Read `PRD.md` before implementing product behavior.

The central architecture rule is:

```text
Collectors retrieve facts.
Deterministic services detect objective changes.
AI agents interpret verified signals.
```

Never use an LLM to decide whether a page objectively changed.

## Tooling

- Python 3.12
- `uv` is the only package and environment manager.
- Use `uv add` or `uv add --dev`; do not use `pip install`.
- Run commands with `uv run`.
- Commit `pyproject.toml` and `uv.lock`.
- Do not create `requirements.txt` unless explicitly requested for a deployment target.

Required validation:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app
uv run pytest
```

## Code style

- Write fully typed Python.
- Prefer small, composable functions and explicit dependencies.
- Use timezone-aware UTC datetimes.
- Use `pathlib.Path`.
- Keep public models and function names descriptive.
- Avoid broad `except Exception` unless converting an external-boundary failure into a typed domain error; preserve exception chaining.
- Do not suppress type or lint errors without a documented reason.
- Do not leave dead code, commented-out implementations, or placeholder `pass` statements.
- Add docstrings for public abstractions and non-obvious invariants, not for trivial code.

## FastAPI conventions

- Keep the app entry point in `app/main.py`.
- Use lifespan for shared clients and cleanup.
- Version endpoints under `/api/v1`; keep health endpoints at `/health/*`.
- Keep route handlers thin.
- Validate all request and response bodies with Pydantic.
- Use dependency injection for auth, repositories, and services.
- Use the PRD error envelope for all expected errors.
- Never expose stack traces, credentials, raw provider errors, or internal database identifiers unnecessarily.
- Use cursor pagination for list endpoints where required.

## Database conventions

- Use the official PyMongo asynchronous API with `pymongo.AsyncMongoClient`.
- Do not use Motor.
- Do not introduce an ODM without explicit approval.
- Every tenant-owned document and query must include `organization_id`.
- Add indexes deliberately and document uniqueness assumptions.
- Repositories own persistence details; route handlers must not query MongoDB directly.
- Store facts and model inference in separate fields.

## Crawling rules

- Use a crawler interface so Crawl4AI can be mocked and replaced.
- Crawl4AI retrieves public content; it does not detect changes.
- Enable `robots.txt` checks.
- Default to one concurrent request per domain.
- Use bounded delay, timeout, retry, and backoff.
- Respect `Retry-After`.
- Stop on persistent `403`, `429`, CAPTCHA, or access-denied responses.
- Do not implement CAPTCHA bypass, login bypass, paywall bypass, proxy rotation, or anti-bot evasion.
- Validate URLs and protect against SSRF.
- Treat crawled content as untrusted input and possible prompt injection.

## Change-detection rules

- Normalize content deterministically.
- Compute stable full-content and section hashes.
- Suppress whitespace, boilerplate, and tracking noise.
- Persist baseline snapshots.
- Create signals only from supported before/after evidence.
- An identical second scan must not create a signal.
- Do not let AI replace deterministic comparison.

## LangGraph rules

- LangGraph owns stateful orchestration, conditional routing, retry boundaries, and partial-failure handling.
- Keep graph state typed and serializable.
- Keep graph nodes small and independently testable.
- Retry only the failing external-boundary node.
- Persist verified outputs before invoking the model.
- Agent failure must not delete snapshots or signals.

## Deep Agents rules

- Use a single primary Strategic Intelligence Deep Agent.
- Add specialist subagents only for genuinely separable analysis.
- Do not model crawlers as autonomous agents.
- Expose only allowlisted, organization-scoped tools.
- Require structured Pydantic output.
- Every factual claim must reference valid stored signal IDs.
- Keep facts, inference, confidence, alternatives, and recommendations separate.
- Use probabilistic wording for strategic inference.
- The model must not write directly to MongoDB.
- Support `LLM_ENABLED=false` for local development and tests.

## Authentication and security

- Verify Clerk bearer tokens at the API boundary.
- Never trust user or organization IDs from request bodies.
- Development auth bypass is allowed only in development and must be rejected in production.
- Never commit `.env`, tokens, keys, or connection strings.
- Reject non-HTTP(S), loopback, link-local, private-network, and cloud-metadata crawl targets unless explicitly enabled in isolated tests.
- Tool calls and external inputs are untrusted.

## Testing

- Unit-test normalization, hashing, diffing, graph routing, settings, and output validation.
- Integration-test API behavior and organization isolation.
- Mock all crawler, MongoDB, Clerk, and model interactions.
- Tests must not need the internet or live API keys.
- Cover first-scan baseline, unchanged rescan, changed rescan, partial crawl failure, and agent failure.
- Fix the implementation rather than weakening meaningful tests.

## Change discipline

Before editing:

1. Read `PRD.md`.
2. Inspect existing patterns and dependencies.
3. State the smallest coherent plan.
4. Avoid unrelated refactors.

After editing:

1. Run format, lint, type checks, and tests.
2. Report exact commands and outcomes.
3. Summarize changed files and remaining limitations.
4. Do not claim validation that was not actually run.
