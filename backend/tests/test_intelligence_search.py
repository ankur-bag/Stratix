import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_authenticated_context
from app.core.auth import AuthenticatedContext
from app.domain.documents import (
    CompanyDocument,
    CompanyKnowledgeChunk,
    DocumentStage,
    DocumentStatus,
)
from app.domain.intelligence_evidence import EvidenceKind, IntelligenceSearchRequest
from app.main import create_app
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import (
    CompetitorEvidenceIndexingService,
    IntelligenceSearchService,
)
from app.services.vector_store import FakeVectorStore, VectorRecord
from tests.fakes import (
    FakeWebsiteCrawler,
    InMemoryPersistence,
    RecordingBackgroundExecutor,
    RecordingCompetitorScanExecutor,
)
from tests.intelligence_helpers import (
    NOW,
    company_profile,
    competitor,
    intelligence_settings,
    snapshot,
)


@pytest.mark.anyio
async def test_search_returns_ranked_canonical_excerpts() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    source = snapshot()
    persistence.competitor_snapshots.items[source.id] = source
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()
    settings = intelligence_settings()
    indexer = CompetitorEvidenceIndexingService(
        persistence=persistence, embeddings=embeddings, vectors=vectors, settings=settings
    )
    await indexer.index_snapshot("org_test", source.id)
    service = IntelligenceSearchService(
        persistence=persistence, embeddings=embeddings, vectors=vectors, settings=settings
    )
    response = await service.search(
        AuthenticatedContext(user_id="user", organization_id="org_test"),
        IntelligenceSearchRequest(query="What does the Pro plan cost?"),
    )
    assert response.bundle.items
    assert response.bundle.items[0].content_excerpt
    assert "normalized_markdown" not in response.model_dump_json()
    query = vectors.queries[-1]
    assert query["namespace"] == "stratix-org-org_test"
    assert query["top_k"] == 30
    assert query["metadata_filter"]["$and"][0] == {"organization_id": {"$eq": "org_test"}}
    assert embeddings.query_calls == ["What does the Pro plan cost?"]


@pytest.mark.anyio
async def test_search_applies_bounded_evidence_kind_filter() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    source = snapshot()
    persistence.competitor_snapshots.items[source.id] = source
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()
    settings = intelligence_settings()
    await CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=settings,
    ).index_snapshot("org_test", source.id)
    service = IntelligenceSearchService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=settings,
    )

    missing = await service.search(
        AuthenticatedContext(user_id="user", organization_id="org_test"),
        IntelligenceSearchRequest(
            query="pricing",
            evidence_kinds=[EvidenceKind.SIGNAL_SUMMARY],
        ),
    )
    found = await service.search(
        AuthenticatedContext(user_id="user", organization_id="org_test"),
        IntelligenceSearchRequest(
            query="pricing",
            evidence_kinds=[EvidenceKind.SNAPSHOT_CHUNK],
        ),
    )

    assert missing.bundle.items == []
    assert found.bundle.items
    assert all(item.evidence_kind == EvidenceKind.SNAPSHOT_CHUNK for item in found.bundle.items)


@pytest.mark.anyio
async def test_search_rejects_controls_foreign_competitors_and_stale_matches() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    source = snapshot()
    persistence.competitor_snapshots.items[source.id] = source
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()
    settings = intelligence_settings()
    await CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=settings,
    ).index_snapshot("org_test", source.id)
    service = IntelligenceSearchService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=settings,
    )
    context = AuthenticatedContext(user_id="user", organization_id="org_test")

    for request, expected in [
        (IntelligenceSearchRequest(query="bad\x00query"), "INTELLIGENCE_QUERY_INVALID"),
        (
            IntelligenceSearchRequest(
                query="pricing",
                competitor_ids=["competitor-from-another-organization"],
            ),
            "INTELLIGENCE_FILTER_INVALID",
        ),
    ]:
        with pytest.raises(Exception) as caught:
            await service.search(context, request)
        assert getattr(caught.value, "code", "") == expected

    for key, item in list(persistence.intelligence_evidence.items.items()):
        persistence.intelligence_evidence.items[key] = item.model_copy(update={"active": False})
    result = await service.search(
        context,
        IntelligenceSearchRequest(query="pricing"),
    )
    assert result.bundle.items == []


@pytest.mark.anyio
async def test_competitor_timeline_deduplicates_chunks_and_paginates_events() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    first = snapshot("snapshot-1", content="Pricing evidence and automation. " * 12)
    second = snapshot("snapshot-2", content="Enterprise controls and reporting. " * 12)
    persistence.competitor_snapshots.items[first.id] = first
    persistence.competitor_snapshots.items[second.id] = second
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()
    settings = intelligence_settings()
    indexer = CompetitorEvidenceIndexingService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=settings,
    )
    await indexer.index_snapshot("org_test", first.id)
    await indexer.index_snapshot("org_test", second.id)
    service = IntelligenceSearchService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=settings,
    )
    context = AuthenticatedContext(user_id="user", organization_id="org_test")

    page_one = await service.timeline(
        context,
        "competitor-1",
        cursor=None,
        limit=1,
        categories=None,
        source_types=None,
        observed_after=None,
        observed_before=None,
        ascending=False,
    )
    page_two = await service.timeline(
        context,
        "competitor-1",
        cursor=page_one.next_cursor,
        limit=1,
        categories=None,
        source_types=None,
        observed_after=None,
        observed_before=None,
        ascending=False,
    )

    assert len(page_one.items) == len(page_two.items) == 1
    assert page_one.next_cursor is not None
    assert page_one.items[0].evidence_id != page_two.items[0].evidence_id


@pytest.mark.anyio
async def test_search_optionally_merges_canonical_company_document_context() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    document = CompanyDocument(
        id="document-1",
        organization_id="org_test",
        company_profile_id="company-1",
        uploaded_by_user_id="user",
        original_filename="strategy.pdf",
        content_type="application/pdf",
        storage_key="private/document-1.pdf",
        size_bytes=1024,
        status=DocumentStatus.READY,
        stage=DocumentStage.COMPLETED,
        progress_percent=100,
        chunk_count=1,
        active_version=1,
        created_at=NOW,
        updated_at=NOW,
        completed_at=NOW,
    )
    chunk = CompanyKnowledgeChunk(
        id="chunk-1",
        organization_id="org_test",
        company_profile_id="company-1",
        document_id=document.id,
        document_version=1,
        chunk_index=0,
        text="Our roadmap prioritizes workflow automation for enterprise customers.",
        text_hash="chunk-hash",
        page_start=2,
        page_end=2,
        vector_id="document-vector-1",
        active=True,
        created_at=NOW,
    )
    persistence.company_documents.items[document.id] = document
    persistence.company_knowledge_chunks.items[chunk.id] = chunk
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()
    await vectors.upsert(
        "stratix-org-org_test",
        [
            VectorRecord(
                id=chunk.vector_id or "",
                values=await embeddings.embed_query("enterprise workflow roadmap"),
                metadata={
                    "organization_id": "org_test",
                    "company_profile_id": "company-1",
                    "document_id": document.id,
                    "document_version": 1,
                    "chunk_id": chunk.id,
                    "chunk_index": 0,
                },
            )
        ],
    )
    response = await IntelligenceSearchService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=intelligence_settings(),
    ).search(
        AuthenticatedContext(user_id="user", organization_id="org_test"),
        IntelligenceSearchRequest(
            query="enterprise workflow roadmap", include_company_context=True
        ),
    )
    assert response.bundle.items[0].source_type.value == "company_document"
    assert response.bundle.items[0].page_start == 2
    assert "private/document-1.pdf" not in response.model_dump_json()


def test_intelligence_search_api_returns_bounded_evidence_excerpts() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    source = snapshot()
    persistence.competitor_snapshots.items[source.id] = source
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()
    settings = intelligence_settings()
    app = create_app(
        settings,
        persistence=persistence,
        background_executor=RecordingBackgroundExecutor(),
        discovery_background_executor=RecordingBackgroundExecutor(),
        competitor_scan_executor=RecordingCompetitorScanExecutor(),
        crawler=FakeWebsiteCrawler(),
        embedding_provider=embeddings,
        vector_store=vectors,
    )

    async def context() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="user", organization_id="org_test")

    app.dependency_overrides[get_authenticated_context] = context
    with TestClient(app) as client:
        asyncio.run(app.state.evidence_indexing_service.index_snapshot("org_test", source.id))
        response = client.post(
            "/api/v1/intelligence/search",
            json={"query": "Pro plan price", "limit": 5},
        )
    assert response.status_code == 200
    assert response.json()["bundle"]["items"]
    assert "normalized_markdown" not in response.text
