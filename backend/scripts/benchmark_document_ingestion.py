"""Offline Phase 4.5 sizing benchmark. No provider credentials or private files."""

import asyncio
import json
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.documents import ExtractedPage, ExtractedPdf
from app.services.embeddings import FakeEmbeddingProvider
from app.services.pdf_processing import DeterministicPdfChunker
from app.services.vector_store import FakeVectorStore, VectorRecord


async def main() -> None:
    pages = [
        ExtractedPage(
            page_number=index + 1,
            text=(
                f"Section {index + 1}\n\n"
                + "\n\n".join(
                    (
                        f"Evidence block {block + 1}. "
                        + "Stratix benchmark content describes products, customers, "
                        "evidence, positioning, pricing, and operating context. " * 16
                    )
                    for block in range(10)
                )
            ),
        )
        for index in range(100)
    ]
    extracted = ExtractedPdf(
        pages=pages,
        page_count=len(pages),
        character_count=sum(len(page.text) for page in pages),
    )
    chunker = DeterministicPdfChunker(
        target_characters=3500,
        maximum_characters=5000,
        overlap_characters=500,
        minimum_characters=150,
    )
    embeddings = FakeEmbeddingProvider()
    vectors = FakeVectorStore()

    tracemalloc.start()
    started = time.perf_counter()
    chunks = chunker.chunk(extracted)
    chunked_at = time.perf_counter()
    values = await embeddings.embed_documents([item.text for item in chunks])
    embedded_at = time.perf_counter()
    records = [
        VectorRecord(
            id=f"benchmark-{index}",
            values=value,
            metadata={"chunk_id": f"chunk-{index}", "document_version": 1},
        )
        for index, value in enumerate(values)
    ]
    upserted = 0
    for offset in range(0, len(records), 100):
        upserted += await vectors.upsert("benchmark-offline", records[offset : offset + 100])
    finished = time.perf_counter()
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    elapsed = finished - started
    report = {
        "mode": "offline_fake",
        "pages": len(pages),
        "extracted_characters": extracted.character_count,
        "chunks": len(chunks),
        "embedding_batches": (len(chunks) + 31) // 32,
        "vectors_upserted": upserted,
        "vector_upsert_batches": (len(records) + 99) // 100,
        "chunking_seconds": round(chunked_at - started, 4),
        "embedding_seconds": round(embedded_at - chunked_at, 4),
        "upsert_seconds": round(finished - embedded_at, 4),
        "total_seconds": round(elapsed, 4),
        "chunks_per_second": round(len(chunks) / elapsed, 2) if elapsed else None,
        "peak_memory_mb": round(peak_bytes / 1024 / 1024, 2),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
