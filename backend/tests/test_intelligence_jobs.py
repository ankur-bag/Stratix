import pytest

from app.domain.intelligence_evidence import EvidenceSourceKind
from app.services.background_jobs import BackgroundJobQueue
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import CompetitorEvidenceIndexingService
from app.services.intelligence_jobs import EvidenceIndexingScheduler
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
async def test_scan_indexing_jobs_are_complete_and_idempotent() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    persistence.competitor_snapshots.items["snapshot-1"] = snapshot("snapshot-1")
    persistence.competitor_signals.items["signal-1"] = signal("signal-1").model_copy(
        update={"scan_id": "scan-1"}
    )
    scheduler = EvidenceIndexingScheduler(
        persistence,
        BackgroundJobQueue(persistence, intelligence_settings()),
    )

    first = await scheduler.schedule_scan(
        organization_id="org_test",
        company_profile_id="company-1",
        scan_id="scan-1",
        competitor_id="competitor-1",
        created_by_user_id="user",
    )
    second = await scheduler.schedule_scan(
        organization_id="org_test",
        company_profile_id="company-1",
        scan_id="scan-1",
        competitor_id="competitor-1",
        created_by_user_id="user",
    )

    assert first == second == {"snapshot_jobs": 1, "signal_jobs": 1}
    assert len(persistence.background_jobs.items) == 2
    assert all(
        "content" not in job.payload and "organization_id" not in job.payload
        for job in persistence.background_jobs.items.values()
    )


@pytest.mark.anyio
async def test_cleanup_is_retry_safe_and_organization_scoped() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    source = snapshot()
    persistence.competitor_snapshots.items[source.id] = source
    vectors = FakeVectorStore()
    service = CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=FakeEmbeddingProvider(),
        vectors=vectors,
        settings=intelligence_settings(),
    )
    await service.index_snapshot("org_test", source.id)
    active_ids = {
        item.vector_id for item in persistence.intelligence_evidence.items.values() if item.active
    }

    assert (
        await service.cleanup_source(
            "other_org",
            EvidenceSourceKind.SNAPSHOT,
            source.id,
        )
        == 0
    )
    assert active_ids <= set(vectors.namespaces["stratix-org-org_test"])
    assert await service.cleanup_source(
        "org_test",
        EvidenceSourceKind.SNAPSHOT,
        source.id,
    ) == len(active_ids)
    assert await service.cleanup_source(
        "org_test",
        EvidenceSourceKind.SNAPSHOT,
        source.id,
    ) == len(active_ids)
    assert not vectors.namespaces["stratix-org-org_test"]
    assert not any(item.active for item in persistence.intelligence_evidence.items.values())
