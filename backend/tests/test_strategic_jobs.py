import pytest

from app.agents.errors import StrategicAgentError
from app.agents.strategic import FakeStrategicAgent, StrategicAgent
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.domain.background_jobs import BackgroundJobStatus, JobType
from app.domain.strategy import StrategyMode, StrategyRunCreate, StrategyRunStatus
from app.services.background_jobs import (
    BackgroundJobQueue,
    BackgroundJobService,
    DurableWorker,
)
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import IntelligenceSearchService
from app.services.strategy import (
    StrategicAnalysisJobHandler,
    StrategicAnalysisRunner,
    StrategyService,
)
from app.services.vector_store import FakeVectorStore
from tests.fakes import InMemoryPersistence
from tests.intelligence_helpers import company_profile, competitor
from tests.strategic_helpers import strategy_evidence, strategy_output


def strategic_runtime(
    persistence: InMemoryPersistence,
    agent: StrategicAgent | None = None,
    settings: Settings | None = None,
) -> tuple[Settings, StrategyService, StrategicAnalysisRunner]:
    settings = settings or Settings(
        app_env="testing",
        background_jobs_enabled=True,
        job_heartbeat_interval_seconds=1,
        job_lease_duration_seconds=10,
        _env_file=None,
    )
    queue = BackgroundJobQueue(persistence, settings)
    background = BackgroundJobService(persistence, queue)
    intelligence = IntelligenceSearchService(
        persistence=persistence,
        embeddings=FakeEmbeddingProvider(),
        vectors=FakeVectorStore(),
        settings=settings,
    )
    service = StrategyService(
        persistence=persistence,
        queue=queue,
        background_jobs=background,
        settings=settings,
    )
    runner = StrategicAnalysisRunner(
        persistence=persistence,
        intelligence=intelligence,
        agent=agent or FakeStrategicAgent(strategy_output(), tool_call_count=2, subagent_count=1),
        settings=settings,
    )
    return settings, service, runner


async def seeded_persistence() -> InMemoryPersistence:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    evidence = strategy_evidence()
    persistence.intelligence_evidence.items[evidence.id] = evidence
    return persistence


@pytest.mark.anyio
async def test_durable_strategic_job_persists_one_validated_report() -> None:
    persistence = await seeded_persistence()
    settings, service, runner = strategic_runtime(persistence)
    context = AuthenticatedContext(user_id="user_test", organization_id="org_test")
    created = await service.create_run(
        context,
        StrategyRunCreate(mode=StrategyMode.ASK, query="What should we prioritize?"),
    )
    worker = DurableWorker(
        persistence=persistence,
        handlers={JobType.STRATEGIC_ANALYSIS: StrategicAnalysisJobHandler(runner)},
        settings=settings,
        worker_id="strategy-worker",
    )
    assert await worker.run_once()
    await worker.drain()
    run = await persistence.strategy_runs.get_by_id("org_test", created.run.id)
    assert run and run.status == StrategyRunStatus.SUCCEEDED
    assert run.report_id
    assert run.cited_evidence_ids == ["evidence-1"]
    assert len(persistence.strategy_reports.items) == 1
    job = next(iter(persistence.background_jobs.items.values()))
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert any(
        notification.type.value == "strategic_report_ready"
        for notification in persistence.notifications.items.values()
    )


@pytest.mark.anyio
async def test_queued_strategic_run_can_be_cancelled_without_report() -> None:
    persistence = await seeded_persistence()
    _, service, _ = strategic_runtime(persistence)
    context = AuthenticatedContext(user_id="user_test", organization_id="org_test")
    created = await service.create_run(
        context,
        StrategyRunCreate(mode=StrategyMode.ASK, query="What changed?"),
    )
    cancelled = await service.cancel_run(context, created.run.id)
    assert cancelled.run.status == StrategyRunStatus.CANCELLED
    assert not persistence.strategy_reports.items


@pytest.mark.anyio
async def test_duplicate_active_request_reuses_run() -> None:
    persistence = await seeded_persistence()
    _, service, _ = strategic_runtime(persistence)
    context = AuthenticatedContext(user_id="user_test", organization_id="org_test")
    request = StrategyRunCreate(mode=StrategyMode.ASK, query="What changed?")
    first = await service.create_run(context, request)
    second = await service.create_run(context, request)
    assert second.run.id == first.run.id
    assert len(persistence.strategy_runs.items) == 1


@pytest.mark.anyio
async def test_retryable_provider_timeout_schedules_durable_retry_with_same_run() -> None:
    persistence = await seeded_persistence()
    agent = FakeStrategicAgent(
        error=StrategicAgentError(
            "STRATEGIC_AGENT_TIMEOUT",
            "The strategic model timed out.",
            retryable=True,
        )
    )
    settings, service, runner = strategic_runtime(persistence, agent=agent)
    context = AuthenticatedContext(user_id="user_test", organization_id="org_test")
    created = await service.create_run(
        context,
        StrategyRunCreate(mode=StrategyMode.ASK, query="What changed?"),
    )
    worker = DurableWorker(
        persistence=persistence,
        handlers={JobType.STRATEGIC_ANALYSIS: StrategicAnalysisJobHandler(runner)},
        settings=settings,
        worker_id="strategy-worker",
    )

    assert await worker.run_once()
    await worker.drain()

    run = await persistence.strategy_runs.get_by_id("org_test", created.run.id)
    job = next(iter(persistence.background_jobs.items.values()))
    assert run and run.id == created.run.id
    assert run.status == StrategyRunStatus.QUEUED
    assert run.stage == "awaiting_retry"
    assert job.status == BackgroundJobStatus.RETRY_SCHEDULED
    assert not persistence.strategy_reports.items


@pytest.mark.anyio
async def test_exhausted_provider_attempt_uses_safe_partial_without_recommendations() -> None:
    persistence = await seeded_persistence()
    agent = FakeStrategicAgent(
        error=StrategicAgentError(
            "STRATEGIC_AGENT_TIMEOUT",
            "The strategic model timed out.",
            retryable=True,
        )
    )
    settings = Settings(
        app_env="testing",
        background_jobs_enabled=True,
        job_max_attempts_default=1,
        job_heartbeat_interval_seconds=1,
        job_lease_duration_seconds=10,
        _env_file=None,
    )
    settings, service, runner = strategic_runtime(
        persistence,
        agent=agent,
        settings=settings,
    )
    context = AuthenticatedContext(user_id="user_test", organization_id="org_test")
    created = await service.create_run(
        context,
        StrategyRunCreate(mode=StrategyMode.ASK, query="What changed?"),
    )
    worker = DurableWorker(
        persistence=persistence,
        handlers={JobType.STRATEGIC_ANALYSIS: StrategicAnalysisJobHandler(runner)},
        settings=settings,
        worker_id="strategy-worker",
    )

    assert await worker.run_once()
    await worker.drain()

    run = await persistence.strategy_runs.get_by_id("org_test", created.run.id)
    report = next(iter(persistence.strategy_reports.items.values()))
    assert run and run.status == StrategyRunStatus.PARTIALLY_SUCCEEDED
    assert report.deterministic_fallback is True
    assert report.recommendations == []
