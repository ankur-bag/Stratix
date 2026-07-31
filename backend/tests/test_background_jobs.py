import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.background_jobs import (
    BackgroundJobStatus,
    JobType,
    NewBackgroundJob,
)
from app.repositories.interfaces import RepositoryConflictError
from app.repositories.memory_jobs import InMemoryBackgroundJobRepository
from app.services.background_jobs import (
    BackgroundJobQueue,
    JobExecutionFailure,
    retry_delay_seconds,
)
from tests.fakes import InMemoryPersistence


def run[T](value) -> T:
    return asyncio.run(value)


def draft(
    now: datetime,
    *,
    organization_id: str = "org-a",
    key: str = "same-key",
    job_type: JobType = JobType.COMPETITOR_RESCAN,
    priority: int = 0,
    available_at: datetime | None = None,
    max_attempts: int = 4,
) -> NewBackgroundJob:
    return NewBackgroundJob(
        organization_id=organization_id,
        job_type=job_type,
        domain_resource_type="competitor_scan",
        domain_resource_id=f"resource-{organization_id}-{key}",
        priority=priority,
        deduplication_key=key,
        available_at=available_at or now,
        max_attempts=max_attempts,
    )


def test_enqueue_deduplicates_within_organization_but_not_across_tenants() -> None:
    repository = InMemoryBackgroundJobRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = run(repository.enqueue(job=draft(now)))
    with pytest.raises(RepositoryConflictError):
        run(repository.enqueue(job=draft(now)))
    second = run(repository.enqueue(job=draft(now, organization_id="org-b", key="same-key")))
    assert first.organization_id == "org-a"
    assert second.organization_id == "org-b"


def test_atomic_claim_obeys_priority_availability_type_and_fair_exclusion() -> None:
    repository = InMemoryBackgroundJobRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    run(repository.enqueue(job=draft(now, key="low", priority=0)))
    high = run(repository.enqueue(job=draft(now, key="high", priority=10)))
    run(
        repository.enqueue(
            job=draft(
                now,
                key="future",
                priority=100,
                available_at=now + timedelta(hours=1),
            )
        )
    )
    run(
        repository.enqueue(
            job=draft(
                now,
                key="document",
                job_type=JobType.DOCUMENT_INGESTION,
            )
        )
    )
    other = run(
        repository.enqueue(job=draft(now, organization_id="org-b", key="other", priority=5))
    )
    claimed = run(
        repository.claim_next(
            worker_id="worker-1",
            supported_job_types={JobType.COMPETITOR_RESCAN},
            now=now,
            lease_duration=timedelta(minutes=2),
        )
    )
    assert claimed and claimed.id == high.id
    fair = run(
        repository.claim_next(
            worker_id="worker-1",
            supported_job_types={JobType.COMPETITOR_RESCAN},
            now=now,
            lease_duration=timedelta(minutes=2),
            excluded_organization_id="org-a",
        )
    )
    assert fair and fair.id == other.id


def test_lease_owner_state_progress_and_cancellation_invariants() -> None:
    repository = InMemoryBackgroundJobRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queued = run(repository.enqueue(job=draft(now)))
    claimed = run(
        repository.claim_next(
            worker_id="worker-a",
            supported_job_types={JobType.COMPETITOR_RESCAN},
            now=now,
            lease_duration=timedelta(seconds=120),
        )
    )
    assert claimed and claimed.attempt_count == 1
    assert not run(
        repository.renew_lease(
            job_id=queued.id,
            worker_id="worker-b",
            now=now,
            lease_duration=timedelta(seconds=120),
        )
    )
    assert run(repository.mark_running(job_id=queued.id, worker_id="worker-b", now=now)) is None
    running = run(repository.mark_running(job_id=queued.id, worker_id="worker-a", now=now))
    assert running and running.status == BackgroundJobStatus.RUNNING
    progress = run(
        repository.update_progress(
            job_id=queued.id,
            worker_id="worker-a",
            now=now,
            progress_percent=40,
            stage="collecting",
            message="Collecting sources.",
        )
    )
    assert progress and progress.progress_percent == 40
    requested = run(repository.request_cancellation("org-a", queued.id, now))
    assert requested and requested.status == BackgroundJobStatus.CANCEL_REQUESTED
    assert run(repository.request_cancellation("org-b", queued.id, now)) is None
    cancelled = run(repository.mark_cancelled(job_id=queued.id, worker_id="worker-a", now=now))
    assert cancelled and cancelled.status == BackgroundJobStatus.CANCELLED


def test_queued_cancellation_is_immediate_and_expired_leases_recover_or_dead_letter() -> None:
    repository = InMemoryBackgroundJobRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queued = run(repository.enqueue(job=draft(now, key="cancel")))
    cancelled = run(repository.request_cancellation("org-a", queued.id, now))
    assert cancelled and cancelled.status == BackgroundJobStatus.CANCELLED

    retryable = run(repository.enqueue(job=draft(now, key="recover")))
    exhausted = run(repository.enqueue(job=draft(now, key="exhausted", max_attempts=1)))
    for _item in (retryable, exhausted):
        claimed = run(
            repository.claim_next(
                worker_id="crashed",
                supported_job_types={JobType.COMPETITOR_RESCAN},
                now=now,
                lease_duration=timedelta(seconds=1),
            )
        )
        assert claimed
    recovered = run(repository.recover_expired_leases(now=now + timedelta(seconds=2), limit=100))
    statuses = {item.deduplication_key: item.status for item in recovered}
    assert statuses["recover"] == BackgroundJobStatus.RETRY_SCHEDULED
    assert statuses["exhausted"] == BackgroundJobStatus.DEAD_LETTERED


def test_backoff_is_bounded_and_deterministic() -> None:
    assert (
        retry_delay_seconds(
            attempt_count=1,
            base_seconds=5,
            maximum_seconds=30,
            jitter_ratio=0.2,
            jitter_source=lambda: 0.5,
        )
        == 5
    )
    assert (
        retry_delay_seconds(
            attempt_count=10,
            base_seconds=5,
            maximum_seconds=30,
            jitter_ratio=0.2,
            jitter_source=lambda: 1,
        )
        == 36
    )


def test_large_queue_claim_is_bounded_and_does_not_materialize_a_copy() -> None:
    repository = InMemoryBackgroundJobRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    async def exercise() -> None:
        for index in range(10_000):
            await repository.enqueue(
                job=draft(
                    now,
                    organization_id=f"org-{index % 100}",
                    key=f"job-{index}",
                    priority=index % 3,
                )
            )
        claimed = await repository.claim_next(
            worker_id="scale-worker",
            supported_job_types={JobType.COMPETITOR_RESCAN},
            now=now,
            lease_duration=timedelta(minutes=2),
        )
        assert claimed is not None
        assert len(repository.items) == 10_000

    run(exercise())


def test_queue_backpressure_rejects_excess_without_creating_a_job() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        settings = Settings(
            app_env="testing",
            max_active_jobs_per_organization=1,
            max_global_queued_jobs=10,
            max_queued_scans_per_organization=10,
            _env_file=None,
        )
        queue = BackgroundJobQueue(persistence, settings)
        await queue.enqueue(
            organization_id="org-a",
            company_profile_id="company-a",
            job_type=JobType.PINECONE_CLEANUP,
            domain_resource_type="company_document",
            domain_resource_id="document-a",
            deduplication_key="cleanup-a",
        )
        with pytest.raises(JobExecutionFailure) as captured:
            await queue.enqueue(
                organization_id="org-a",
                company_profile_id="company-a",
                job_type=JobType.PINECONE_CLEANUP,
                domain_resource_type="company_document",
                domain_resource_id="document-b",
                deduplication_key="cleanup-b",
            )
        assert captured.value.code == "ORGANIZATION_JOB_LIMIT_REACHED"
        assert len(persistence.background_jobs.items) == 1

    run(exercise())


def test_job_api_is_organization_scoped(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    now = datetime.now(UTC)
    own = run(
        persistence.background_jobs.enqueue(job=draft(now, organization_id="org_test", key="own"))
    )
    foreign = run(
        persistence.background_jobs.enqueue(job=draft(now, organization_id="org-b", key="foreign"))
    )
    response = client.get("/api/v1/jobs")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [own.id]
    assert client.get(f"/api/v1/jobs/{foreign.id}").status_code == 404
    assert client.post(f"/api/v1/jobs/{foreign.id}/cancel").status_code == 404
    assert client.post(f"/api/v1/jobs/{foreign.id}/retry").status_code == 404
