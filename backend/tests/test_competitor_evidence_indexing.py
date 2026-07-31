import pytest

from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import CompetitorEvidenceIndexingService
from app.services.vector_store import FakeVectorStore, VectorStoreError
from tests.fakes import InMemoryPersistence
from tests.intelligence_helpers import (
    company_profile,
    competitor,
    intelligence_settings,
    snapshot,
)


@pytest.mark.anyio
async def test_failed_reindex_preserves_previous_active_evidence() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    vectors = FakeVectorStore()
    service = CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=FakeEmbeddingProvider(),
        vectors=vectors,
        settings=intelligence_settings(),
    )
    first = snapshot("snapshot-versioned")
    persistence.competitor_snapshots.items[first.id] = first
    await service.index_snapshot("org_test", first.id)
    old_ids = {item.id for item in persistence.intelligence_evidence.items.values() if item.active}
    second = snapshot(
        first.id,
        content="A replacement pricing page with a $59 Pro plan. " * 8,
    ).model_copy(update={"content_hash": "replacement-content-hash"})
    persistence.competitor_snapshots.items[second.id] = second
    vectors.transient_failures = 1
    with pytest.raises(VectorStoreError):
        await service.index_snapshot("org_test", second.id)
    active_ids = {
        item.id for item in persistence.intelligence_evidence.items.values() if item.active
    }
    assert active_ids == old_ids


@pytest.mark.anyio
async def test_lost_lease_before_activation_preserves_previous_version() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    vectors = FakeVectorStore()
    service = CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=FakeEmbeddingProvider(),
        vectors=vectors,
        settings=intelligence_settings(),
    )
    first = snapshot("snapshot-versioned")
    persistence.competitor_snapshots.items[first.id] = first
    await service.index_snapshot("org_test", first.id)
    old_ids = {item.id for item in persistence.intelligence_evidence.items.values() if item.active}
    persistence.competitor_snapshots.items[first.id] = snapshot(
        first.id,
        content="A replacement plan adds enterprise governance and costs $99. " * 6,
    ).model_copy(update={"content_hash": "lease-loss-version"})

    async def lease_was_lost() -> bool:
        return False

    with pytest.raises(Exception) as caught:
        await service.index_snapshot(
            "org_test",
            first.id,
            before_activation=lease_was_lost,
        )

    assert getattr(caught.value, "code", "") == "INTELLIGENCE_INDEXING_CANCELLED"
    assert {
        item.id for item in persistence.intelligence_evidence.items.values() if item.active
    } == old_ids
