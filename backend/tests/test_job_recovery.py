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
)
from tests.fakes import InMemoryPersistence


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class SuccessHandler:
    job_type = JobType.PINECONE_CLEANUP

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        return JobExecutionResult(result_summary={"idempotent_cleanup": True})


def test_api_restart_then_worker_completion_and_crash_lease_recovery() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        clock = FakeClock(now)
        settings = Settings(
            app_env="testing",
            job_lease_duration_seconds=10,
            job_heartbeat_interval_seconds=2,
            _env_file=None,
        )
        queue = BackgroundJobQueue(persistence, settings, clock)
        job = await queue.enqueue(
            organization_id="org-a",
            company_profile_id="company-a",
            job_type=JobType.PINECONE_CLEANUP,
            domain_resource_type="company_document",
            domain_resource_id="document-a",
            deduplication_key="restart-safe",
        )
        worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.PINECONE_CLEANUP: SuccessHandler()},
            settings=settings,
            worker_id="worker-a",
            clock=clock,
        )
        assert await worker.run_once()
        await worker.drain()
        assert persistence.background_jobs.items[job.id].status == BackgroundJobStatus.SUCCEEDED

        crash_job = await queue.enqueue(
            organization_id="org-a",
            company_profile_id="company-a",
            job_type=JobType.PINECONE_CLEANUP,
            domain_resource_type="company_document",
            domain_resource_id="document-b",
            deduplication_key="crash-safe",
        )
        claimed = await persistence.background_jobs.claim_next(
            worker_id="crashed-worker",
            supported_job_types={JobType.PINECONE_CLEANUP},
            now=clock.now(),
            lease_duration=timedelta(seconds=10),
        )
        assert claimed and claimed.id == crash_job.id
        await persistence.background_jobs.mark_running(
            job_id=crash_job.id,
            worker_id="crashed-worker",
            now=clock.now(),
        )
        clock.value += timedelta(seconds=11)
        second_worker = DurableWorker(
            persistence=persistence,
            handlers={JobType.PINECONE_CLEANUP: SuccessHandler()},
            settings=settings,
            worker_id="worker-b",
            clock=clock,
        )
        recovered = await second_worker.recover()
        assert recovered[0].status == BackgroundJobStatus.RETRY_SCHEDULED
        assert await second_worker.run_once()
        await second_worker.drain()
        assert (
            persistence.background_jobs.items[crash_job.id].status == BackgroundJobStatus.SUCCEEDED
        )

    asyncio.run(exercise())
