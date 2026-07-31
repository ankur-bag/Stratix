import asyncio
import sys
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.auth import AuthenticatedContext
from app.domain.intelligence_evidence import (
    EvidenceKind,
    EvidenceSourceKind,
    EvidenceSourceType,
    IntelligenceCategory,
    IntelligenceEvidence,
    IntelligenceSearchRequest,
)
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import IntelligenceSearchService
from app.services.vector_store import FakeVectorStore, VectorRecord
from tests.fakes import InMemoryPersistence
from tests.intelligence_helpers import company_profile, competitor, intelligence_settings


async def main() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()
    settings = intelligence_settings()
    now = datetime.now(UTC)
    records: list[VectorRecord] = []
    pending: list[tuple[IntelligenceEvidence, str]] = []
    evidence_count = 1_000
    competitor_count = 50
    signal_count = evidence_count // 5
    snapshot_count = evidence_count - signal_count
    for index in range(50):
        item = competitor(f"competitor-{index}")
        persistence.competitors.items[item.id] = item
    tracemalloc.start()
    started = perf_counter()
    for index in range(evidence_count):
        competitor_id = f"competitor-{index % competitor_count}"
        is_signal = index % 5 == 0
        content = (
            f"Competitor {index % competitor_count} pricing and product evidence version {index}. "
            "The public plan includes automation, reporting, and integrations."
        )
        evidence = IntelligenceEvidence(
            id=f"evidence-{index:05d}",
            organization_id="org_test",
            company_profile_id="company-1",
            competitor_id=competitor_id,
            snapshot_id=None if is_signal else f"snapshot-{index}",
            signal_id=f"signal-{index}" if is_signal else None,
            source_type=(
                EvidenceSourceType.COMPETITOR_PRICING_SIGNAL
                if is_signal
                else EvidenceSourceType.COMPETITOR_WEBSITE_SNAPSHOT
            ),
            source_kind=(EvidenceSourceKind.SIGNAL if is_signal else EvidenceSourceKind.SNAPSHOT),
            evidence_kind=(
                EvidenceKind.SIGNAL_SUMMARY if is_signal else EvidenceKind.SNAPSHOT_CHUNK
            ),
            category=(IntelligenceCategory.PRICING if index % 2 else IntelligenceCategory.FEATURES),
            title=f"Evidence {index}",
            content=content,
            content_hash=f"hash-{index}",
            processing_version="benchmark-v1",
            version_key=f"version-{index}",
            active=True,
            observed_at=now - timedelta(days=index % 365),
            created_at=now,
            updated_at=now,
            vector_id=f"vector-{index:05d}",
            embedding_provider="fake",
            embedding_model="fake",
            embedding_version=embeddings.version,
            embedding_dimensions=embeddings.dimensions,
        )
        persistence.intelligence_evidence.items[evidence.id] = evidence
        pending.append((evidence, content))
    for offset in range(0, len(pending), 32):
        batch = pending[offset : offset + 32]
        embedded = await embeddings.embed_documents([content for _, content in batch])
        records.extend(
            VectorRecord(
                id=evidence.vector_id,
                values=vector,
                metadata={
                    "organization_id": "org_test",
                    "company_profile_id": "company-1",
                    "competitor_id": evidence.competitor_id or "",
                    "evidence_id": evidence.id,
                    "source_type": evidence.source_type.value,
                    "source_kind": evidence.source_kind.value,
                    "evidence_kind": evidence.evidence_kind.value,
                    "evidence_version_key": evidence.version_key,
                    "category": evidence.category.value,
                    "observed_at_epoch": int(evidence.observed_at.timestamp()),
                    "active": True,
                    "deleted": False,
                },
            )
            for (evidence, _), vector in zip(batch, embedded, strict=True)
        )
    for offset in range(0, len(records), 100):
        await vectors.upsert("stratix-org-org_test", records[offset : offset + 100])
    setup_elapsed = perf_counter() - started
    service = IntelligenceSearchService(
        persistence=persistence,
        embeddings=embeddings,
        vectors=vectors,
        settings=settings,
    )
    latencies: list[float] = []
    authorized_matches = 0
    search_queries = 100
    for index in range(search_queries):
        query_started = perf_counter()
        response = await service.search(
            AuthenticatedContext(user_id="benchmark", organization_id="org_test"),
            IntelligenceSearchRequest(
                query=f"pricing automation competitor {index % competitor_count}", limit=10
            ),
        )
        authorized_matches += len(response.bundle.items)
        latencies.append((perf_counter() - query_started) * 1000)
    latencies.sort()
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    retrieval_candidates = sum(int(item["top_k"]) for item in vectors.queries)
    print(f"evidence_records={evidence_count}")
    print(f"snapshots_processed={snapshot_count}")
    print(f"signals_processed={signal_count}")
    print(f"chunks_generated={snapshot_count}")
    print(f"embedding_batches={len(embeddings.document_calls)}")
    print(f"vector_batches={(len(records) + 99) // 100}")
    print(f"search_queries={search_queries}")
    print(f"retrieval_candidates={retrieval_candidates}")
    print(f"authorized_matches={authorized_matches}")
    print("deduplicated_matches=0")
    print(f"bounded_discarded_matches={retrieval_candidates - authorized_matches}")
    print(f"setup_seconds={setup_elapsed:.3f}")
    print(f"duration_seconds={sum(latencies) / 1000:.3f}")
    print(f"peak_memory_mib={peak_bytes / 1024 / 1024:.3f}")
    print(f"p50_ms={latencies[49]:.3f}")
    print(f"p95_ms={latencies[94]:.3f}")
    print(f"max_ms={latencies[-1]:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
