import pytest

from app.domain.intelligence import NormalizedSection
from app.domain.intelligence_evidence import (
    EvidenceKind,
    EvidenceSourceKind,
    IntelligenceAnswerRequest,
    IntelligenceCategory,
    IntelligenceSearchRequest,
)
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import CompetitorEvidenceIndexingService, CompetitorSnapshotChunker
from app.services.vector_store import FakeVectorStore
from tests.fakes import InMemoryPersistence
from tests.intelligence_helpers import (
    company_profile,
    competitor,
    intelligence_settings,
    signal,
    snapshot,
)


def test_snapshot_chunking_is_deterministic_bounded_and_deduplicated() -> None:
    chunker = CompetitorSnapshotChunker(
        target_characters=80,
        maximum_characters=120,
        overlap_characters=15,
        minimum_characters=20,
    )
    section = NormalizedSection(
        heading="Features",
        content=("Workflow automation improves review speed. " * 10).strip(),
        content_hash="hash",
    )
    first = chunker.chunk([section, section])
    second = chunker.chunk([section])
    assert first == second
    assert first
    assert all(20 <= len(content) <= 120 for _, content in first)
    assert all(heading == "Features" for heading, _ in first)


def test_snapshot_chunking_skips_empty_and_noise_only_sections() -> None:
    chunker = CompetitorSnapshotChunker(
        target_characters=80,
        maximum_characters=120,
        overlap_characters=15,
        minimum_characters=20,
    )
    assert (
        chunker.chunk(
            [
                NormalizedSection(heading="Empty", content=" \n\t ", content_hash="empty"),
                NormalizedSection(heading="Tiny", content="Cookie", content_hash="tiny"),
            ]
        )
        == []
    )


def test_public_search_and_answer_filter_shapes_are_supported() -> None:
    search = IntelligenceSearchRequest.model_validate(
        {
            "query": "pricing changes",
            "signal_categories": ["pricing"],
            "date_from": "2026-05-01T00:00:00Z",
            "date_to": "2026-07-31T23:59:59Z",
        }
    )
    answer = IntelligenceAnswerRequest.model_validate(
        {
            "query": "positioning changes",
            "date_range": {
                "from": "2026-05-01T00:00:00Z",
                "to": "2026-07-31T23:59:59Z",
            },
        }
    )

    assert search.categories == [IntelligenceCategory.PRICING]
    assert search.observed_after is not None
    assert search.observed_before is not None
    assert answer.observed_after == search.observed_after
    assert answer.observed_before == search.observed_before


@pytest.mark.anyio
async def test_evidence_activation_is_version_scoped() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    first = snapshot()
    persistence.competitor_snapshots.items[first.id] = first
    service = CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=FakeEmbeddingProvider(),
        vectors=FakeVectorStore(),
        settings=intelligence_settings(),
    )
    assert await service.index_snapshot("org_test", first.id) > 0
    stored = list(persistence.intelligence_evidence.items.values())
    assert stored and all(item.active for item in stored)
    assert all(item.source_kind == EvidenceSourceKind.SNAPSHOT for item in stored)
    assert all(item.evidence_kind == EvidenceKind.SNAPSHOT_CHUNK for item in stored)
    assert all(item.section_heading == "Pricing" for item in stored)
    assert all(item.source_url == first.final_url for item in stored)
    assert all(item.observed_at == first.fetched_at for item in stored)
    ids = [item.id for item in stored]
    hashes = [item.content_hash for item in stored]
    assert await service.index_snapshot("org_test", first.id) == len(stored)
    assert [item.id for item in persistence.intelligence_evidence.items.values()] == ids
    assert [
        item.content_hash for item in persistence.intelligence_evidence.items.values()
    ] == hashes


@pytest.mark.anyio
async def test_signal_projection_is_deterministic_and_preserves_change_evidence() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    source = signal()
    persistence.competitor_signals.items[source.id] = source
    vectors = FakeVectorStore()
    service = CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=FakeEmbeddingProvider(),
        vectors=vectors,
        settings=intelligence_settings(),
    )

    assert await service.index_signal("org_test", source.id) == 1
    item = next(iter(persistence.intelligence_evidence.items.values()))
    assert item.evidence_kind == EvidenceKind.SIGNAL_SUMMARY
    assert source.summary in item.content
    assert f"Before: {source.before_excerpt}" in item.content
    assert f"After: {source.after_excerpt}" in item.content
    assert item.observed_at == source.observed_at
    assert item.source_url == source.source_url
    vector = vectors.namespaces["stratix-org-org_test"][item.vector_id]
    assert vector.metadata["evidence_kind"] == "signal_summary"
    assert vector.metadata["signal_id"] == source.id
    assert vector.metadata["evidence_version_key"] == item.version_key
