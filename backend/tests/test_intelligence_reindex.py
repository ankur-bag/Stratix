import pytest

from app.core.auth import AuthenticatedContext
from app.domain.background_jobs import JobType
from app.domain.intelligence_evidence import IntelligenceReindexRequest
from app.services.background_jobs import BackgroundJobQueue
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import CompetitorEvidenceIndexingService
from app.services.intelligence_jobs import IntelligenceReindexService
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
async def test_reindex_is_one_durable_organization_scoped_job() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    service = IntelligenceReindexService(
        persistence, BackgroundJobQueue(persistence, intelligence_settings())
    )
    response = await service.schedule(
        AuthenticatedContext(user_id="admin", organization_id="org_test"),
        IntelligenceReindexRequest(competitor_ids=["competitor-1"]),
    )
    job = persistence.background_jobs.items[response.job_id]
    assert job.job_type == JobType.COMPETITOR_INTELLIGENCE_REINDEX
    assert job.organization_id == "org_test"
    with pytest.raises(Exception) as caught:
        await service.schedule(
            AuthenticatedContext(user_id="admin", organization_id="org_test"),
            IntelligenceReindexRequest(),
        )
    assert getattr(caught.value, "code", "") == "INTELLIGENCE_REINDEX_ALREADY_ACTIVE"


@pytest.mark.anyio
async def test_reindex_rebuilds_all_retained_canonical_evidence() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    persistence.competitor_snapshots.items["snapshot-1"] = snapshot("snapshot-1")
    persistence.competitor_snapshots.items["snapshot-2"] = snapshot(
        "snapshot-2",
        content="The Team plan costs $79 and includes advanced reporting. " * 4,
    )
    persistence.competitor_signals.items["signal-1"] = signal("signal-1")
    service = CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=FakeEmbeddingProvider(),
        vectors=FakeVectorStore(),
        settings=intelligence_settings(
            competitor_evidence_database_batch_size=1,
        ),
    )

    summary = await service.reindex_organization("org_test")

    active = [
        item
        for item in persistence.intelligence_evidence.items.values()
        if item.organization_id == "org_test" and item.active
    ]
    assert summary == {"indexed_count": len(active), "failed_count": 0}
    assert summary["indexed_count"] >= 3
    assert {item.snapshot_id for item in active if item.source_kind.value == "snapshot"} == {
        "snapshot-1",
        "snapshot-2",
    }
    assert {item.signal_id for item in active if item.source_kind.value == "signal"} == {"signal-1"}
