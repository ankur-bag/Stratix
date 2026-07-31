import pytest

from app.core.auth import AuthenticatedContext
from app.domain.background_jobs import BackgroundJobStatus, JobType
from app.domain.intelligence_evidence import IntelligenceAnswerRequest
from app.services.background_jobs import BackgroundJobQueue, DurableWorker
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import CompetitorEvidenceIndexingService, IntelligenceSearchService
from app.services.intelligence_jobs import EvidenceIndexingJobHandler, EvidenceIndexingScheduler
from app.services.vector_store import FakeVectorStore
from tests.fakes import InMemoryPersistence
from tests.intelligence_helpers import (
    company_profile,
    competitor,
    intelligence_settings,
    signal,
    snapshot,
)


@pytest.mark.anyio
async def test_canonical_scan_output_flows_through_worker_to_cited_answer() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    persistence.competitor_snapshots.items["snapshot-1"] = snapshot("snapshot-1")
    persistence.competitor_signals.items["signal-1"] = signal("signal-1").model_copy(
        update={"scan_id": "scan-1"}
    )
    settings = intelligence_settings(
        job_heartbeat_interval_seconds=1,
        job_lease_duration_seconds=5,
    )
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()
    indexing = CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=settings,
    )
    await EvidenceIndexingScheduler(
        persistence,
        BackgroundJobQueue(persistence, settings),
    ).schedule_scan(
        organization_id="org_test",
        company_profile_id="company-1",
        scan_id="scan-1",
        competitor_id="competitor-1",
        created_by_user_id="user",
    )
    worker = DurableWorker(
        persistence=persistence,
        handlers={
            JobType.COMPETITOR_SNAPSHOT_INDEXING: EvidenceIndexingJobHandler(
                JobType.COMPETITOR_SNAPSHOT_INDEXING,
                indexing,
            ),
            JobType.COMPETITOR_SIGNAL_INDEXING: EvidenceIndexingJobHandler(
                JobType.COMPETITOR_SIGNAL_INDEXING,
                indexing,
            ),
        },
        settings=settings,
        worker_id="intelligence-worker",
    )

    assert await worker.run_once()
    await worker.drain()
    assert await worker.run_once()
    await worker.drain()

    assert {job.status for job in persistence.background_jobs.items.values()} == {
        BackgroundJobStatus.SUCCEEDED
    }
    search = IntelligenceSearchService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=settings,
    )
    answer = await search.answer(
        AuthenticatedContext(user_id="user", organization_id="org_test"),
        IntelligenceAnswerRequest(query="What changed in the Pro plan pricing?"),
    )
    returned_ids = {item.evidence_id for item in answer.bundle.items}
    assert returned_ids
    assert {item.evidence_id for item in answer.citations} <= returned_ids
    assert all(
        item.organization_id == "org_test" and item.active
        for item in persistence.intelligence_evidence.items.values()
    )
