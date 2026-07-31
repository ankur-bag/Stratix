import pytest

from app.core.auth import AuthenticatedContext
from app.domain.intelligence_evidence import IntelligenceSearchRequest
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import CompetitorEvidenceIndexingService, IntelligenceSearchService
from app.services.vector_store import FakeVectorStore, VectorRecord
from tests.fakes import InMemoryPersistence
from tests.intelligence_helpers import company_profile, competitor, intelligence_settings, snapshot


@pytest.mark.anyio
async def test_vector_metadata_cannot_bypass_mongo_tenant_validation() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    source = snapshot()
    persistence.competitor_snapshots.items[source.id] = source
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()
    settings = intelligence_settings()
    await CompetitorEvidenceIndexingService(
        persistence=persistence, embeddings=embeddings, vectors=vectors, settings=settings
    ).index_snapshot("org_test", source.id)
    query = await embeddings.embed_query("pricing")
    namespace = "stratix-org-org_test"
    await vectors.upsert(
        namespace,
        [
            VectorRecord(
                id="poison",
                values=query,
                metadata={
                    "organization_id": "org_test",
                    "company_profile_id": "company-1",
                    "competitor_id": "competitor-1",
                    "evidence_id": "foreign-evidence",
                    "source_type": "competitor_signal",
                    "source_kind": "signal",
                    "category": "pricing",
                    "observed_at_epoch": 0,
                    "active": True,
                    "deleted": False,
                },
            )
        ],
    )
    service = IntelligenceSearchService(
        persistence=persistence, embeddings=embeddings, vectors=vectors, settings=settings
    )
    result = await service.search(
        AuthenticatedContext(user_id="user", organization_id="org_test"),
        IntelligenceSearchRequest(query="pricing"),
    )
    assert "foreign-evidence" not in {item.evidence_id for item in result.bundle.items}
