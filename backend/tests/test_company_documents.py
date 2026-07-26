import asyncio
import hashlib
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi import FastAPI

from app.api.dependencies import get_authenticated_context
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.domain.documents import ExtractedPage, ExtractedPdf
from app.main import create_app
from app.services.company_documents import (
    namespace_for_organization,
    vector_id,
)
from app.services.document_storage import (
    FakeObjectStorage,
    LocalObjectStorage,
    StorageSizeExceededError,
)
from app.services.embeddings import (
    EmbeddingError,
    FakeEmbeddingProvider,
    GeminiEmbeddingProvider,
)
from app.services.pdf_processing import (
    DeterministicPdfChunker,
    DocumentProcessingError,
    PdfExtractor,
)
from app.services.vector_store import (
    FakeVectorStore,
    PineconeVectorStore,
    VectorRecord,
    VectorStoreError,
)
from tests.fakes import (
    FakeWebsiteCrawler,
    InMemoryPersistence,
    RecordingBackgroundExecutor,
    RecordingCompetitorScanExecutor,
)
from tests.test_onboarding import valid_payload


def pdf_bytes(*pages: str) -> bytes:
    document = pymupdf.open()
    for value in pages:
        page = document.new_page()
        page.insert_textbox(
            pymupdf.Rect(36, 36, page.rect.width - 36, page.rect.height - 36),
            value,
            fontsize=11,
        )
    data = document.tobytes()
    document.close()
    return data


def encrypted_pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Private encrypted content")
    data = document.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )
    document.close()
    return data


def upload_pdf(client, data: bytes, filename: str = "company-brief.pdf") -> str:
    requested = client.post(
        "/api/v1/company-documents/upload-requests",
        json={
            "filename": filename,
            "content_type": "application/pdf",
            "size_bytes": len(data),
        },
    )
    assert requested.status_code == 201
    document_id = requested.json()["document"]["id"]
    assert requested.json()["upload"]["url"].endswith(f"/{document_id}/content")
    assert (
        client.put(
            f"/api/v1/company-documents/{document_id}/content",
            content=data,
            headers={"Content-Type": "application/pdf"},
        ).status_code
        == 200
    )
    completed = client.post(
        f"/api/v1/company-documents/{document_id}/complete",
        json={
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        },
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "uploaded"
    return document_id


def test_upload_attach_and_ingest_pdf(client, persistence: InMemoryPersistence) -> None:
    data = pdf_bytes(
        "Company brief\n\nAvento helps support teams answer customer questions. "
        "The platform offers private knowledge search and workflow automation."
    )
    document_id = upload_pdf(client, data)
    payload = valid_payload()
    payload["document_ids"] = [document_id]

    response = client.post(
        "/api/v1/onboarding/company",
        headers={"Idempotency-Key": "onboarding-document-001"},
        json=payload,
    )

    assert response.status_code == 202
    document = persistence.company_documents.items[document_id]
    assert document.company_profile_id == response.json()["company_profile"]["id"]
    assert document.ingestion_job_id is not None


def test_phase45_configuration_defaults_match_storage_embedding_and_vector_contracts() -> None:
    settings = Settings(app_env="testing", _env_file=None)
    assert settings.document_max_files_per_company == 10
    assert settings.document_onboarding_max_files == 1
    assert settings.document_max_file_size_bytes == 20_971_520
    assert settings.document_max_pages == 100
    assert settings.document_max_extracted_characters == 2_000_000
    assert (
        settings.document_chunk_target_characters,
        settings.document_chunk_max_characters,
        settings.document_chunk_overlap_characters,
        settings.document_chunk_min_characters,
    ) == (3500, 5000, 500, 150)
    assert settings.embedding_model == "gemini-embedding-001"
    assert settings.embedding_dimensions == settings.pinecone_dimensions == 768
    assert settings.embedding_document_task_type == "RETRIEVAL_DOCUMENT"
    assert settings.embedding_query_task_type == "RETRIEVAL_QUERY"
    assert settings.pinecone_index_name == "stratix-knowledge"
    assert settings.pinecone_metric == "cosine"
    assert settings.pinecone_namespace_prefix == "stratix-org-"
    assert namespace_for_organization("stratix-org-", "org_123") == ("stratix-org-org_123")
    assert vector_id("document", 2, 7, "abcdef1234567890") == ("ck-document-2-7-abcdef123456")


def test_upload_rejects_non_pdf_and_mismatched_completion(client) -> None:
    unsupported = client.post(
        "/api/v1/company-documents/upload-requests",
        json={"filename": "notes.txt", "content_type": "text/plain", "size_bytes": 10},
    )
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "DOCUMENT_UNSUPPORTED_TYPE"

    data = pdf_bytes("Valid content")
    requested = client.post(
        "/api/v1/company-documents/upload-requests",
        json={
            "filename": "brief.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(data),
        },
    ).json()
    document_id = requested["document"]["id"]
    client.put(f"/api/v1/company-documents/{document_id}/content", content=data)
    mismatch = client.post(
        f"/api/v1/company-documents/{document_id}/complete",
        json={"sha256": "0" * 64, "size_bytes": len(data)},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "DOCUMENT_UPLOAD_MISMATCH"

    declared = client.post(
        "/api/v1/company-documents/upload-requests",
        json={
            "filename": "declared-size.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(data) + 1,
        },
    ).json()
    declared_id = declared["document"]["id"]
    client.put(f"/api/v1/company-documents/{declared_id}/content", content=data)
    declared_mismatch = client.post(
        f"/api/v1/company-documents/{declared_id}/complete",
        json={
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        },
    )
    assert declared_mismatch.status_code == 409
    assert declared_mismatch.json()["error"]["code"] == "DOCUMENT_UPLOAD_MISMATCH"


def test_upload_enforces_declared_size_and_company_file_count(client) -> None:
    too_large = client.post(
        "/api/v1/company-documents/upload-requests",
        json={
            "filename": "large.pdf",
            "content_type": "application/pdf",
            "size_bytes": 20_971_521,
        },
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "DOCUMENT_SIZE_LIMIT_EXCEEDED"

    for index in range(10):
        response = client.post(
            "/api/v1/company-documents/upload-requests",
            json={
                "filename": f"brief-{index}.pdf",
                "content_type": "application/pdf",
                "size_bytes": 100,
            },
        )
        assert response.status_code == 201
    limit = client.post(
        "/api/v1/company-documents/upload-requests",
        json={
            "filename": "brief-over-limit.pdf",
            "content_type": "application/pdf",
            "size_bytes": 100,
        },
    )
    assert limit.status_code == 409
    assert limit.json()["error"]["code"] == "DOCUMENT_LIMIT_EXCEEDED"


def test_cross_organization_document_ids_are_not_disclosed(application: FastAPI, client) -> None:
    document_id = upload_pdf(client, pdf_bytes("Private organization content"))

    async def other_organization() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="same_user", organization_id="org_other")

    application.dependency_overrides[get_authenticated_context] = other_organization
    response = client.get(f"/api/v1/company-documents/{document_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_pdf_extraction_limits_and_image_only_failure() -> None:
    valid = asyncio.run(
        PdfExtractor(maximum_pages=2, maximum_characters=10_000).extract(
            pdf_bytes("Text on the first page", "Text on the second page")
        )
    )
    assert valid.page_count == 2
    assert valid.character_count > 0

    with pytest.raises(DocumentProcessingError) as page_error:
        asyncio.run(
            PdfExtractor(maximum_pages=1, maximum_characters=10_000).extract(
                pdf_bytes("one", "two")
            )
        )
    assert page_error.value.code == "DOCUMENT_PAGE_LIMIT_EXCEEDED"

    with pytest.raises(DocumentProcessingError) as text_error:
        asyncio.run(PdfExtractor(maximum_pages=2, maximum_characters=10_000).extract(pdf_bytes("")))
    assert text_error.value.code == "DOCUMENT_NO_EXTRACTABLE_TEXT"

    with pytest.raises(DocumentProcessingError) as encrypted_error:
        asyncio.run(
            PdfExtractor(maximum_pages=2, maximum_characters=10_000).extract(encrypted_pdf_bytes())
        )
    assert encrypted_error.value.code == "DOCUMENT_ENCRYPTED"


def test_local_storage_streams_hashes_and_removes_oversized_partial_file(
    tmp_path,
) -> None:
    async def chunks():
        yield b"abc"
        yield b"def"

    async def scenario() -> None:
        storage = LocalObjectStorage(tmp_path)
        await storage.start()
        with pytest.raises(StorageSizeExceededError):
            await storage.put_stream("org/document.pdf", chunks(), maximum_size=5)
        assert await storage.stat("org/document.pdf") is None
        assert not list(tmp_path.rglob("*.partial"))

    asyncio.run(scenario())


def test_chunking_is_deterministic_bounded_and_page_aware() -> None:
    extracted = ExtractedPdf(
        pages=[
            ExtractedPage(
                page_number=index,
                text=f"SECTION {index}\n\n" + (f"Page {index} evidence sentence. " * 180),
            )
            for index in range(1, 5)
        ],
        page_count=4,
        character_count=22_000,
    )
    chunker = DeterministicPdfChunker(
        target_characters=3500,
        maximum_characters=5000,
        overlap_characters=500,
        minimum_characters=150,
    )
    first = chunker.chunk(extracted)
    second = chunker.chunk(extracted)
    assert first == second
    assert all(len(item.text) <= 5000 for item in first)
    assert all(item.page_start <= item.page_end for item in first)
    assert [item.index for item in first] == list(range(len(first)))


def test_fake_vector_store_models_namespace_filter_cosine_and_delete() -> None:
    async def scenario() -> None:
        store = FakeVectorStore(dimensions=3)
        await store.upsert(
            "stratix-org-a",
            [
                VectorRecord("one", [1.0, 0.0, 0.0], {"document_id": "d1"}),
                VectorRecord("two", [0.0, 1.0, 0.0], {"document_id": "d2"}),
            ],
        )
        await store.upsert(
            "stratix-org-b",
            [VectorRecord("secret", [1.0, 0.0, 0.0], {"document_id": "d3"})],
        )
        matches = await store.query(
            "stratix-org-a",
            [1.0, 0.0, 0.0],
            top_k=3,
            metadata_filter={"document_id": {"$in": ["d1"]}},
        )
        assert [item.id for item in matches] == ["one"]
        await store.delete("stratix-org-a", ids=["one"])
        remaining = await store.query("stratix-org-a", [1.0, 0.0, 0.0], top_k=3)
        assert [item.id for item in remaining] == ["two"]
        assert await store.query("stratix-org-b", [1.0, 0.0, 0.0], top_k=3)

    asyncio.run(scenario())


def test_gemini_embedding_batches_task_types_dimensions_and_retries() -> None:
    class TransientError(Exception):
        status_code = 429

    class Models:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], object]] = []
            self.failures = 1

        async def embed_content(self, *, model, contents, config):
            assert model == "gemini-embedding-001"
            self.calls.append((list(contents), config))
            if self.failures:
                self.failures -= 1
                raise TransientError
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.25] * 768) for _ in contents]
            )

    models = Models()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    provider = GeminiEmbeddingProvider(
        api_key="test",
        model="gemini-embedding-001",
        dimensions=768,
        document_task_type="RETRIEVAL_DOCUMENT",
        query_task_type="RETRIEVAL_QUERY",
        batch_size=32,
        maximum_concurrency=2,
        maximum_retries=3,
        retry_base_seconds=0,
        version="gemini-embedding-001-768-v1",
        client=client,
    )

    vectors = asyncio.run(provider.embed_documents([f"document {index}" for index in range(70)]))
    query = asyncio.run(provider.embed_query("pricing"))

    assert len(vectors) == 70
    assert len(query) == 768
    assert [len(call[0]) for call in models.calls] == [32, 32, 6, 32, 1]
    assert models.calls[0][1].task_type == "RETRIEVAL_DOCUMENT"
    assert models.calls[-1][1].task_type == "RETRIEVAL_QUERY"
    assert models.calls[-1][1].output_dimensionality == 768


def test_gemini_embedding_rejects_invalid_dimensions_without_retrying() -> None:
    class Models:
        def __init__(self) -> None:
            self.calls = 0

        async def embed_content(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 2.0])])

    models = Models()
    provider = GeminiEmbeddingProvider(
        api_key="test",
        model="gemini-embedding-001",
        dimensions=768,
        document_task_type="RETRIEVAL_DOCUMENT",
        query_task_type="RETRIEVAL_QUERY",
        batch_size=32,
        maximum_concurrency=2,
        maximum_retries=3,
        retry_base_seconds=0,
        version="test",
        client=SimpleNamespace(aio=SimpleNamespace(models=models)),
    )
    with pytest.raises(EmbeddingError) as error:
        asyncio.run(provider.embed_documents(["text"]))
    assert error.value.code == "EMBEDDING_DIMENSION_MISMATCH"
    assert models.calls == 1


def test_gemini_embedding_does_not_retry_non_transient_failures() -> None:
    class InvalidRequest(Exception):
        status_code = 400

    class Models:
        def __init__(self) -> None:
            self.calls = 0

        async def embed_content(self, **kwargs):
            self.calls += 1
            raise InvalidRequest

    models = Models()
    provider = GeminiEmbeddingProvider(
        api_key="test",
        model="gemini-embedding-001",
        dimensions=768,
        document_task_type="RETRIEVAL_DOCUMENT",
        query_task_type="RETRIEVAL_QUERY",
        batch_size=32,
        maximum_concurrency=2,
        maximum_retries=3,
        retry_base_seconds=0,
        version="test",
        client=SimpleNamespace(aio=SimpleNamespace(models=models)),
    )
    with pytest.raises(EmbeddingError) as error:
        asyncio.run(provider.embed_query("query"))
    assert error.value.code == "EMBEDDING_PROVIDER_ERROR"
    assert models.calls == 1


def test_pinecone_adapter_validates_existing_index_and_batches_upserts() -> None:
    class Index:
        def __init__(self) -> None:
            self.upserts: list[tuple[str, list[dict[str, object]]]] = []
            self.query_arguments: dict[str, object] = {}
            self.closed = False

        async def upsert(self, *, namespace, vectors):
            self.upserts.append((namespace, vectors))
            return SimpleNamespace(upserted_count=len(vectors))

        async def query(self, **kwargs):
            self.query_arguments = kwargs
            return SimpleNamespace(
                matches=[
                    SimpleNamespace(
                        id="v1",
                        score=0.9,
                        metadata={"chunk_id": "chunk-1"},
                    )
                ]
            )

        async def delete(self, **kwargs):
            return None

        async def close(self):
            self.closed = True

    class Client:
        def __init__(self) -> None:
            self.index = Index()
            self.closed = False

        async def describe_index(self, name):
            assert name == "stratix-knowledge"
            return SimpleNamespace(
                dimension=768,
                metric="cosine",
                status=SimpleNamespace(ready=True),
            )

        def IndexAsyncio(self, *, host):
            assert host == "configured-index-host"
            return self.index

        async def close(self):
            self.closed = True

    async def scenario() -> None:
        client = Client()
        store = PineconeVectorStore(
            api_key="test",
            host="configured-index-host",
            index_name="stratix-knowledge",
            dimensions=768,
            metric="cosine",
            upsert_batch_size=100,
            maximum_concurrency=2,
            maximum_retries=3,
            retry_base_seconds=0,
            client=client,
        )
        await store.start()
        records = [
            VectorRecord(
                id=f"v-{index}",
                values=[0.0] * 768,
                metadata={"chunk_id": f"c-{index}"},
            )
            for index in range(205)
        ]
        assert await store.upsert("stratix-org-test", records) == 205
        assert sorted(len(batch) for _, batch in client.index.upserts) == [5, 100, 100]
        matches = await store.query(
            "stratix-org-test",
            [0.0] * 768,
            top_k=8,
            metadata_filter={"document_version": {"$eq": 1}},
        )
        assert matches[0].id == "v1"
        assert client.index.query_arguments["include_values"] is False
        assert client.index.query_arguments["namespace"] == "stratix-org-test"
        await store.close()
        assert client.index.closed and client.closed

    asyncio.run(scenario())


def test_pinecone_adapter_rejects_wrong_manual_index_dimension() -> None:
    class Client:
        async def describe_index(self, name):
            return SimpleNamespace(
                dimension=1536,
                metric="cosine",
                status=SimpleNamespace(ready=True),
            )

    store = PineconeVectorStore(
        api_key="test",
        host="host",
        index_name="stratix-knowledge",
        dimensions=768,
        metric="cosine",
        upsert_batch_size=100,
        maximum_concurrency=2,
        maximum_retries=0,
        retry_base_seconds=0,
        client=Client(),
    )
    with pytest.raises(VectorStoreError) as error:
        asyncio.run(store.start())
    assert error.value.code == "VECTOR_INDEX_DIMENSION_MISMATCH"


def test_pinecone_adapter_rejects_partial_upsert_acknowledgement() -> None:
    class Index:
        async def upsert(self, *, namespace, vectors):
            return SimpleNamespace(upserted_count=len(vectors) - 1)

    class Client:
        def __init__(self) -> None:
            self.index = Index()

        async def describe_index(self, name):
            return SimpleNamespace(
                dimension=768,
                metric="cosine",
                status=SimpleNamespace(ready=True),
            )

        def IndexAsyncio(self, *, host):
            return self.index

    async def scenario() -> None:
        store = PineconeVectorStore(
            api_key="test",
            host="host",
            index_name="stratix-knowledge",
            dimensions=768,
            metric="cosine",
            upsert_batch_size=100,
            maximum_concurrency=2,
            maximum_retries=0,
            retry_base_seconds=0,
            client=Client(),
        )
        await store.start()
        with pytest.raises(VectorStoreError) as error:
            await store.upsert(
                "stratix-org-test",
                [VectorRecord("id", [0.0] * 768, {"chunk_id": "chunk"})],
            )
        assert error.value.code == "VECTOR_UPSERT_PARTIAL"

    asyncio.run(scenario())


def test_optional_vector_startup_failure_degrades_but_required_failure_stops_readiness() -> None:
    class FailingVectorStore(FakeVectorStore):
        async def start(self) -> None:
            raise VectorStoreError("VECTOR_STORE_UNAVAILABLE", "Vector service unavailable.")

    def application(required: bool) -> FastAPI:
        settings = Settings(
            app_env="testing",
            embeddings_enabled=True,
            gemini_api_key="test-placeholder",
            vector_search_enabled=True,
            vector_search_required=required,
            vector_db_provider="fake",
            auto_start_competitor_discovery=False,
            _env_file=None,
        )
        app = create_app(
            settings,
            persistence=InMemoryPersistence(),
            crawler=FakeWebsiteCrawler(),
            background_executor=RecordingBackgroundExecutor(),
            discovery_background_executor=RecordingBackgroundExecutor(),
            competitor_scan_executor=RecordingCompetitorScanExecutor(),
            document_storage=FakeObjectStorage(),
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=FailingVectorStore(),
            document_background_executor=RecordingBackgroundExecutor(),
        )

        async def context() -> AuthenticatedContext:
            return AuthenticatedContext(user_id="user", organization_id="org")

        app.dependency_overrides[get_authenticated_context] = context
        return app

    from fastapi.testclient import TestClient

    with TestClient(application(required=False)) as client:
        assert client.get("/health/ready").status_code == 200

    with (
        pytest.raises(VectorStoreError) as error,
        TestClient(application(required=True)),
    ):
        pass
    assert error.value.code == "VECTOR_STORE_UNAVAILABLE"


def test_full_ingestion_and_search_uses_canonical_mongo_excerpt() -> None:
    persistence = InMemoryPersistence()
    storage = FakeObjectStorage()
    embeddings = FakeEmbeddingProvider(dimensions=768)
    vectors = FakeVectorStore(dimensions=768)
    document_executor = RecordingBackgroundExecutor()
    settings = Settings(
        app_env="testing",
        vector_search_enabled=True,
        vector_db_provider="fake",
        embeddings_enabled=True,
        gemini_api_key="test-placeholder",
        auto_start_competitor_discovery=False,
        _env_file=None,
    )
    app = create_app(
        settings,
        persistence=persistence,
        crawler=FakeWebsiteCrawler(),
        background_executor=RecordingBackgroundExecutor(),
        discovery_background_executor=RecordingBackgroundExecutor(),
        competitor_scan_executor=RecordingCompetitorScanExecutor(),
        document_storage=storage,
        embedding_provider=embeddings,
        vector_store=vectors,
        document_background_executor=document_executor,
    )

    async def context() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="user", organization_id="org_search")

    app.dependency_overrides[get_authenticated_context] = context
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        data = pdf_bytes(
            "PRICING\n\nThe private company plan costs 49 dollars monthly. "
            "It includes evidence search and unlimited projects."
        )
        document_id = upload_pdf(client, data)
        payload = valid_payload()
        payload["document_ids"] = [document_id]
        onboarded = client.post(
            "/api/v1/onboarding/company",
            headers={"Idempotency-Key": "search-onboarding-001"},
            json=payload,
        )
        assert onboarded.status_code == 202
        job_id = document_executor.scheduled[0][0]
        asyncio.run(
            app.state.document_ingestion_runner.run(job_id=job_id, organization_id="org_search")
        )
        response = client.post(
            "/api/v1/company/knowledge/search",
            json={"query": "monthly plan price", "top_k": 5},
        )
        assert response.status_code == 200
        assert response.json()["vector_search_enabled"] is True
        assert response.json()["items"][0]["document_id"] == document_id
        assert "49 dollars" in response.json()["items"][0]["excerpt"]
        query_filter = vectors.queries[-1]["metadata_filter"]
        assert query_filter["$and"][0] == {"organization_id": {"$eq": "org_search"}}
        assert query_filter["$and"][1] == {
            "company_profile_id": {"$eq": onboarded.json()["company_profile"]["id"]}
        }
        assert query_filter["$and"][2]["$or"][0]["$and"][1] == {"document_version": {"$eq": 1}}

        stored_document = persistence.company_documents.items[document_id]
        assert stored_document.active_version == 1
        storage.objects[stored_document.storage_key] = b"not-a-pdf"
        reprocessed = client.post(f"/api/v1/company-documents/{document_id}/reprocess")
        assert reprocessed.status_code == 202
        failed_job_id = reprocessed.json()["id"]
        asyncio.run(
            app.state.document_ingestion_runner.run(
                job_id=failed_job_id, organization_id="org_search"
            )
        )
        preserved = persistence.company_documents.items[document_id]
        assert preserved.status == "ready"
        assert preserved.active_version == 1
        assert preserved.warning_codes == ["DOCUMENT_REPROCESS_FAILED"]
        assert persistence.document_ingestion_jobs.items[failed_job_id].status == "failed"

        still_searchable = client.post(
            "/api/v1/company/knowledge/search",
            json={"query": "monthly plan price", "top_k": 5},
        )
        assert still_searchable.status_code == 200
        assert "49 dollars" in still_searchable.json()["items"][0]["excerpt"]

        deleted = client.delete(f"/api/v1/company-documents/{document_id}")
        assert deleted.status_code == 204
        assert stored_document.storage_key not in storage.objects
        assert not persistence.company_knowledge_chunks.items
        assert not vectors.namespaces["stratix-org-org_search"]


@pytest.mark.parametrize(
    ("website_succeeds", "document_succeeds", "expected_status", "warning"),
    [
        (True, True, "ready", None),
        (False, True, "ready", "WEBSITE_COLLECTION_FAILED"),
        (True, False, "ready", "DOCUMENT_PROCESSING_FAILED"),
        (False, False, "failed", None),
    ],
)
def test_initialization_combines_website_and_document_evidence(
    website_succeeds: bool,
    document_succeeds: bool,
    expected_status: str,
    warning: str | None,
) -> None:
    persistence = InMemoryPersistence()
    storage = FakeObjectStorage()
    initialization_executor = RecordingBackgroundExecutor()
    document_executor = RecordingBackgroundExecutor()
    settings = Settings(
        app_env="testing",
        auto_start_competitor_discovery=False,
        _env_file=None,
    )
    crawler = FakeWebsiteCrawler() if website_succeeds else FakeWebsiteCrawler(pages=[])
    app = create_app(
        settings,
        persistence=persistence,
        crawler=crawler,
        background_executor=initialization_executor,
        discovery_background_executor=RecordingBackgroundExecutor(),
        competitor_scan_executor=RecordingCompetitorScanExecutor(),
        document_storage=storage,
        document_background_executor=document_executor,
    )

    async def context() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="user", organization_id="org_evidence")

    app.dependency_overrides[get_authenticated_context] = context
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        data = pdf_bytes(
            "COMPANY EVIDENCE\n\nThe private brief confirms the product and customer segment."
        )
        document_id = upload_pdf(client, data)
        payload = valid_payload()
        payload["document_ids"] = [document_id]
        onboarded = client.post(
            "/api/v1/onboarding/company",
            headers={"Idempotency-Key": (f"evidence-{website_succeeds}-{document_succeeds}-001")},
            json=payload,
        )
        assert onboarded.status_code == 202
        if not document_succeeds:
            storage_key = persistence.company_documents.items[document_id].storage_key
            storage.objects[storage_key] = b"invalid-pdf"

        document_job_id = document_executor.scheduled[0][0]
        asyncio.run(
            app.state.document_ingestion_runner.run(
                job_id=document_job_id, organization_id="org_evidence"
            )
        )
        initialization_job_id = onboarded.json()["initialization_job"]["id"]
        asyncio.run(
            app.state.initialization_runner.run(
                job_id=initialization_job_id,
                organization_id="org_evidence",
            )
        )

        company = next(iter(persistence.company_profiles.items.values()))
        job = persistence.initialization_jobs.items[initialization_job_id]
        assert company.onboarding_status == expected_status
        assert job.status == ("succeeded" if expected_status == "ready" else "failed")
        if warning:
            assert company.workspace_metadata["initialization_warning"] == warning
