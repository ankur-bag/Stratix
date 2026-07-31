from datetime import UTC, datetime

import pytest

from app.core.auth import AuthenticatedContext
from app.domain.intelligence_evidence import (
    EvidenceKind,
    EvidenceSourceKind,
    EvidenceSourceType,
    IntelligenceCategory,
    IntelligenceEvidence,
    IntelligenceSearchRequest,
)
from app.services.company_documents import namespace_for_organization
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import CompetitorEvidenceIndexingService, IntelligenceSearchService
from app.services.vector_store import FakeVectorStore, VectorMatch, VectorRecord
from tests.fakes import InMemoryPersistence
from tests.intelligence_helpers import (
    company_profile,
    competitor,
    intelligence_settings,
    signal,
    snapshot,
)


def test_large_multi_organization_set_has_unique_derived_namespaces() -> None:
    namespaces = {
        namespace_for_organization("stratix-org-", f"org-{index:03d}") for index in range(100)
    }

    assert len(namespaces) == 100
    assert "" not in namespaces
    assert all(namespace.startswith("stratix-org-org-") for namespace in namespaces)


@pytest.mark.anyio
async def test_reindex_paginates_large_retained_history() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    competitor_count = 50
    snapshots_per_competitor = 100
    signals_per_competitor = 20
    for competitor_index in range(competitor_count):
        competitor_id = f"competitor-{competitor_index:02d}"
        persistence.competitors.items[competitor_id] = competitor(competitor_id)
        for snapshot_index in range(snapshots_per_competitor):
            identifier = f"snapshot-{competitor_index:02d}-{snapshot_index:03d}"
            persistence.competitor_snapshots.items[identifier] = snapshot(
                identifier,
                competitor_id=competitor_id,
            )
        for signal_index in range(signals_per_competitor):
            identifier = f"signal-{competitor_index:02d}-{signal_index:03d}"
            persistence.competitor_signals.items[identifier] = signal(
                identifier,
                competitor_id=competitor_id,
            )

    class CountingReindexer:
        def __init__(self) -> None:
            self._persistence = persistence
            self._settings = intelligence_settings(competitor_evidence_database_batch_size=37)
            self.snapshot_ids: list[str] = []
            self.signal_ids: list[str] = []

        async def index_snapshot(self, organization_id: str, snapshot_id: str) -> int:
            assert organization_id == "org_test"
            self.snapshot_ids.append(snapshot_id)
            return 1

        async def index_signal(self, organization_id: str, signal_id: str) -> int:
            assert organization_id == "org_test"
            self.signal_ids.append(signal_id)
            return 1

    service = CountingReindexer()
    summary = await CompetitorEvidenceIndexingService.reindex_organization(
        service,  # type: ignore[arg-type]
        "org_test",
    )

    assert summary == {
        "indexed_count": competitor_count * (snapshots_per_competitor + signals_per_competitor),
        "failed_count": 0,
    }
    assert len(set(service.snapshot_ids)) == competitor_count * snapshots_per_competitor
    assert len(set(service.signal_ids)) == competitor_count * signals_per_competitor


@pytest.mark.anyio
async def test_large_fake_namespace_keeps_query_and_results_bounded() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    embeddings = FakeEmbeddingProvider()

    class LightweightVectorStore:
        enabled = True

        def __init__(self) -> None:
            self.namespaces: dict[str, list[VectorRecord]] = {}
            self.queries: list[dict[str, object]] = []

        async def upsert(self, namespace: str, items: list[VectorRecord]) -> int:
            self.namespaces.setdefault(namespace, []).extend(items)
            return len(items)

        async def query(
            self,
            namespace: str,
            vector: list[float],
            *,
            top_k: int,
            metadata_filter: dict[str, object] | None = None,
        ) -> list[VectorMatch]:
            del vector
            self.queries.append(
                {
                    "namespace": namespace,
                    "top_k": top_k,
                    "metadata_filter": metadata_filter or {},
                }
            )
            return [
                VectorMatch(item.id, 1.0, item.metadata)
                for item in self.namespaces.get(namespace, [])
                if FakeVectorStore._matches(item.metadata, metadata_filter or {})
            ][:top_k]

        async def delete(
            self,
            namespace: str,
            *,
            ids: list[str] | None = None,
            metadata_filter: dict[str, object] | None = None,
        ) -> None:
            del namespace, ids, metadata_filter

    vectors = LightweightVectorStore()
    now = datetime.now(UTC)
    total_vectors = 100_000
    records = [
        VectorRecord(
            id=f"vector-{index:06d}",
            values=[],
            metadata={
                "organization_id": "org_test",
                "company_profile_id": "company-1",
                "competitor_id": "competitor-1",
                "evidence_id": f"evidence-{index:06d}",
                "source_type": "competitor_website_snapshot",
                "source_kind": "snapshot",
                "evidence_kind": "snapshot_chunk",
                "category": "features",
                "evidence_version_key": f"version-{index}",
                "observed_at_epoch": int(now.timestamp()),
                "active": True,
                "deleted": False,
            },
        )
        for index in range(total_vectors)
    ]
    await vectors.upsert("stratix-org-org_test", records)
    for index in range(30):
        evidence = IntelligenceEvidence(
            id=f"evidence-{index:06d}",
            organization_id="org_test",
            company_profile_id="company-1",
            competitor_id="competitor-1",
            snapshot_id=f"snapshot-{index}",
            source_type=EvidenceSourceType.COMPETITOR_WEBSITE_SNAPSHOT,
            source_kind=EvidenceSourceKind.SNAPSHOT,
            evidence_kind=EvidenceKind.SNAPSHOT_CHUNK,
            category=IntelligenceCategory.FEATURES,
            title=f"Feature evidence {index}",
            content="Enterprise automation and workflow evidence.",
            content_hash=f"hash-{index}",
            processing_version="scale-v1",
            version_key=f"version-{index}",
            active=True,
            observed_at=now,
            created_at=now,
            updated_at=now,
            vector_id=f"vector-{index:06d}",
            embedding_provider="fake",
            embedding_model="fake",
            embedding_version=embeddings.version,
            embedding_dimensions=768,
        )
        persistence.intelligence_evidence.items[evidence.id] = evidence
    service = IntelligenceSearchService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=intelligence_settings(),
    )

    response = await service.search(
        AuthenticatedContext(user_id="user", organization_id="org_test"),
        IntelligenceSearchRequest(query="enterprise features", limit=10),
    )

    assert len(response.bundle.items) == 10
    assert vectors.queries[-1]["namespace"] == "stratix-org-org_test"
    assert vectors.queries[-1]["top_k"] == 30
    assert "" not in vectors.namespaces
