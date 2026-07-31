import asyncio
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.domain.background_jobs import (
    BackgroundJob,
    BackgroundJobStatus,
    JobExecutionResult,
    JobType,
)
from app.services.background_jobs import (
    BackgroundJobQueue,
    DurableWorker,
    JobExecutionContext,
    JobExecutionFailure,
)
from tests.fakes import InMemoryPersistence


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class ResultHandler:
    job_type = JobType.PINECONE_CLEANUP

    def __init__(self, result: JobExecutionResult | BaseException) -> None:
        self.result = result
        self.calls = 0

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def settings(**changes) -> Settings:
    values = {
        "app_env": "testing",
        "job_heartbeat_interval_seconds": 1,
        "job_lease_duration_seconds": 3,
        "worker_graceful_shutdown_seconds": 1,
        **changes,
    }
    return Settings(**values, _env_file=None)


async def queued(
    persistence: InMemoryPersistence,
    clock: FakeClock,
    *,
    max_attempts: int | None = None,
) -> BackgroundJob:
    config = settings(
        job_max_attempts_default=max_attempts or 4,
    )
    return await BackgroundJobQueue(persistence, config, clock).enqueue(
        organization_id="org-a",
        company_profile_id="company-a",
        job_type=JobType.PINECONE_CLEANUP,
        domain_resource_type="company_document",
        domain_resource_id="document-a",
        deduplication_key="cleanup-a",
    )


def test_worker_executes_claimed_job_and_marks_success() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        clock = FakeClock()
        job = await queued(persistence, clock)
        handler = ResultHandler(JobExecutionResult(result_summary={"vectors_deleted": 2}))
        worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.PINECONE_CLEANUP: handler},
            settings=settings(),
            worker_id="worker-a",
            clock=clock,
        )
        assert await worker.run_once()
        await worker.drain()
        completed = persistence.background_jobs.items[job.id]
        assert completed.status == BackgroundJobStatus.SUCCEEDED
        assert completed.result_summary == {"vectors_deleted": 2}
        assert handler.calls == 1

    asyncio.run(exercise())


def test_worker_schedules_retry_without_sleep_and_dead_letters_after_limit() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        clock = FakeClock()
        retry_job = await queued(persistence, clock)
        handler = ResultHandler(
            JobExecutionFailure(
                "TEMPORARY_PROVIDER_FAILURE",
                "Provider unavailable.",
                retryable=True,
            )
        )
        worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.PINECONE_CLEANUP: handler},
            settings=settings(),
            worker_id="worker-a",
            clock=clock,
            jitter_source=lambda: 0.5,
        )
        await worker.run_once()
        await worker.drain()
        retried = persistence.background_jobs.items[retry_job.id]
        assert retried.status == BackgroundJobStatus.RETRY_SCHEDULED
        assert retried.available_at > clock.now()
        assert not persistence.notifications.items

        persistence = InMemoryPersistence()
        exhausted = await queued(persistence, clock, max_attempts=1)
        worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.PINECONE_CLEANUP: handler},
            settings=settings(job_max_attempts_default=1),
            worker_id="worker-b",
            clock=clock,
            jitter_source=lambda: 0.5,
        )
        await worker.run_once()
        await worker.drain()
        assert (
            persistence.background_jobs.items[exhausted.id].status
            == BackgroundJobStatus.DEAD_LETTERED
        )

    asyncio.run(exercise())


def test_permanent_error_fails_immediately_and_lost_lease_discards_success() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        clock = FakeClock()
        failed_job = await queued(persistence, clock)
        permanent = ResultHandler(
            JobExecutionFailure("DOCUMENT_INVALID_PDF", "Invalid PDF.", retryable=False)
        )
        worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.PINECONE_CLEANUP: permanent},
            settings=settings(),
            worker_id="worker-a",
            clock=clock,
        )
        await worker.run_once()
        await worker.drain()
        assert persistence.background_jobs.items[failed_job.id].status == BackgroundJobStatus.FAILED

        persistence = InMemoryPersistence()
        stolen = await queued(persistence, clock)

        class LeaseStealingHandler(ResultHandler):
            async def execute(
                self, *, job: BackgroundJob, context: JobExecutionContext
            ) -> JobExecutionResult:
                current = persistence.background_jobs.items[job.id]
                persistence.background_jobs.items[job.id] = current.model_copy(
                    update={"worker_id": "worker-b"}
                )
                return JobExecutionResult()

        worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.PINECONE_CLEANUP: LeaseStealingHandler(JobExecutionResult())},
            settings=settings(),
            worker_id="worker-a",
            clock=clock,
        )
        await worker.run_once()
        await worker.drain()
        assert persistence.background_jobs.items[stolen.id].status == BackgroundJobStatus.RUNNING

    asyncio.run(exercise())


def test_progress_updates_are_throttled_until_the_percent_delta_is_material() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        clock = FakeClock()
        queued_job = await queued(persistence, clock)
        claimed = await persistence.background_jobs.claim_next(
            worker_id="worker-a",
            supported_job_types={JobType.PINECONE_CLEANUP},
            now=clock.now(),
            lease_duration=timedelta(seconds=30),
        )
        assert claimed and claimed.id == queued_job.id
        running = await persistence.background_jobs.mark_running(
            job_id=queued_job.id,
            worker_id="worker-a",
            now=clock.now(),
        )
        assert running
        context = JobExecutionContext(
            persistence=persistence,
            clock=clock,
            worker_id="worker-a",
            lease_lost=asyncio.Event(),
            shutdown_requested=asyncio.Event(),
            progress_min_interval_seconds=100,
            progress_min_percent_delta=5,
        )
        assert await context.report_progress(running, 1, "one", "First update")
        assert await context.report_progress(running, 2, "two", "Suppressed update")
        assert persistence.background_jobs.items[running.id].progress_percent == 1
        assert await context.report_progress(running, 6, "six", "Material update")
        assert persistence.background_jobs.items[running.id].progress_percent == 6

    asyncio.run(exercise())


def test_graceful_shutdown_stops_claiming_but_renews_active_job_lease() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        clock = FakeClock()
        job = await queued(persistence, clock)
        started = asyncio.Event()
        release = asyncio.Event()
        cancellation_seen = False

        class GracefulHandler(ResultHandler):
            async def execute(
                self, *, job: BackgroundJob, context: JobExecutionContext
            ) -> JobExecutionResult:
                nonlocal cancellation_seen
                started.set()
                await release.wait()
                cancellation_seen = await context.cancelled(job)
                return JobExecutionResult()

        worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.PINECONE_CLEANUP: GracefulHandler(JobExecutionResult())},
            settings=settings(worker_graceful_shutdown_seconds=3),
            worker_id="worker-a",
            clock=clock,
        )
        assert await worker.run_once()
        await started.wait()
        clock.value += timedelta(seconds=10)
        shutdown = asyncio.create_task(worker.shutdown())
        await asyncio.sleep(1.1)
        active = persistence.background_jobs.items[job.id]
        assert active.heartbeat_at == clock.now()
        assert not await worker.run_once()
        release.set()
        await shutdown
        assert not cancellation_seen
        assert persistence.background_jobs.items[job.id].status == BackgroundJobStatus.SUCCEEDED
        completed_heartbeat = persistence.background_jobs.items[job.id].heartbeat_at
        clock.value += timedelta(seconds=10)
        await asyncio.sleep(1.1)
        assert persistence.background_jobs.items[job.id].heartbeat_at == completed_heartbeat

    asyncio.run(exercise())


def test_worker_bounds_global_concurrency_and_dead_letters_missing_handler() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        clock = FakeClock()
        release = asyncio.Event()
        both_started = asyncio.Event()
        active = 0
        maximum_active = 0

        class BlockingHandler(ResultHandler):
            async def execute(
                self, *, job: BackgroundJob, context: JobExecutionContext
            ) -> JobExecutionResult:
                nonlocal active, maximum_active
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 2:
                    both_started.set()
                await release.wait()
                active -= 1
                return JobExecutionResult()

        queue = BackgroundJobQueue(persistence, settings(), clock)
        for index in range(3):
            await queue.enqueue(
                organization_id=f"org-{index}",
                company_profile_id=f"company-{index}",
                job_type=JobType.PINECONE_CLEANUP,
                domain_resource_type="company_document",
                domain_resource_id=f"document-{index}",
                deduplication_key=f"cleanup-{index}",
            )
        worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.PINECONE_CLEANUP: BlockingHandler(JobExecutionResult())},
            settings=settings(
                worker_max_concurrency=2,
                cleanup_job_max_concurrency=2,
            ),
            worker_id="bounded-worker",
            clock=clock,
        )
        assert await worker.run_once()
        assert await worker.run_once()
        assert not await worker.run_once()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        assert maximum_active == 2
        release.set()
        await worker.drain()

        unsupported = await queue.enqueue(
            organization_id="org-unsupported",
            company_profile_id=None,
            job_type=JobType.RETENTION_CLEANUP,
            domain_resource_type="organization",
            domain_resource_id="org-unsupported",
            deduplication_key="unsupported",
        )
        worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.RETENTION_CLEANUP: None},  # type: ignore[dict-item]
            settings=settings(),
            worker_id="unsupported-worker",
            clock=clock,
        )
        assert await worker.run_once()
        await worker.drain()
        assert (
            persistence.background_jobs.items[unsupported.id].status
            == BackgroundJobStatus.DEAD_LETTERED
        )

    asyncio.run(exercise())
