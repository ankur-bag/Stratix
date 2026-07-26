import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.documents import (
    CompanyDocument,
    CompanyKnowledgeChunk,
    DocumentIngestionJob,
    DocumentJobStatus,
    DocumentStage,
    DocumentStatus,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
    UploadCompleteRequest,
    UploadRequest,
    UploadRequestResponse,
    UploadTarget,
)
from app.domain.onboarding import new_id, utc_now
from app.repositories.interfaces import Persistence, RepositoryUnavailableError
from app.services.document_storage import (
    ObjectStorage,
    StorageSizeExceededError,
)
from app.services.embeddings import EmbeddingError, EmbeddingProvider
from app.services.initialization import BackgroundExecutor
from app.services.pdf_processing import (
    DeterministicPdfChunker,
    DocumentProcessingError,
    PdfExtractor,
)
from app.services.vector_store import VectorRecord, VectorStore, VectorStoreError

logger = get_logger(__name__)


def namespace_for_organization(prefix: str, organization_id: str) -> str:
    if not organization_id:
        raise ValueError("Organization is required for a vector namespace.")
    return f"{prefix}{organization_id}"


def vector_id(document_id: str, version: int, index: int, text_hash: str) -> str:
    return f"ck-{document_id}-{version}-{index}-{text_hash[:12]}"


class DocumentIngestionRunner:
    def __init__(
        self,
        *,
        persistence: Persistence,
        storage: ObjectStorage,
        extractor: PdfExtractor,
        chunker: DeterministicPdfChunker,
        embeddings: EmbeddingProvider,
        vectors: VectorStore,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._storage = storage
        self._extractor = extractor
        self._chunker = chunker
        self._embeddings = embeddings
        self._vectors = vectors
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.document_ingestion_max_concurrency)

    async def run(self, *, job_id: str, organization_id: str) -> None:
        async with self._semaphore:
            await self._run(job_id=job_id, organization_id=organization_id)

    async def _run(self, *, job_id: str, organization_id: str) -> None:
        job = await self._persistence.document_ingestion_jobs.get_by_id(organization_id, job_id)
        if job is None:
            return
        document = await self._persistence.company_documents.get_by_id(
            organization_id, job.document_id
        )
        if document is None or document.company_profile_id != job.company_profile_id:
            await self._fail(job, "DOCUMENT_NOT_FOUND", "The document was not found.")
            return
        pending_vector_ids: list[str] = []
        try:
            await self._progress(job, DocumentStage.VALIDATING, 5, "Validating PDF.")
            try:
                data = await self._storage.read(document.storage_key)
            except Exception as exc:
                raise DocumentProcessingError(
                    "DOCUMENT_STORAGE_UNAVAILABLE",
                    "The private document is temporarily unavailable.",
                ) from exc
            extracted = await self._extractor.extract(data)
            await self._progress(
                job,
                DocumentStage.EXTRACTING_TEXT,
                25,
                "Extracting document text.",
                {
                    "page_count": extracted.page_count,
                    "extracted_character_count": extracted.character_count,
                },
            )
            drafts = self._chunker.chunk(extracted)
            if not drafts:
                raise DocumentProcessingError(
                    "DOCUMENT_NO_EXTRACTABLE_TEXT",
                    "No usable document text was found.",
                )
            await self._progress(
                job,
                DocumentStage.CHUNKING,
                45,
                "Preparing company knowledge.",
                {"chunk_count": len(drafts)},
            )
            now = utc_now()
            chunks = [
                CompanyKnowledgeChunk(
                    id=new_id(),
                    organization_id=organization_id,
                    company_profile_id=job.company_profile_id,
                    document_id=document.id,
                    document_version=job.version,
                    chunk_index=draft.index,
                    text=draft.text,
                    text_hash=draft.text_hash,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    heading=draft.heading,
                    embedding_version=(
                        self._embeddings.version if self._embeddings.enabled else None
                    ),
                    vector_id=(
                        vector_id(document.id, job.version, draft.index, draft.text_hash)
                        if self._embeddings.enabled and self._vectors.enabled
                        else None
                    ),
                    active=False,
                    created_at=now,
                )
                for draft in drafts
            ]
            pending_vector_ids = [item.vector_id for item in chunks if item.vector_id is not None]
            for offset in range(0, len(chunks), self._settings.document_database_batch_size):
                await self._persistence.company_knowledge_chunks.create_many(
                    chunks[offset : offset + self._settings.document_database_batch_size]
                )

            warnings = list(extracted.warnings)
            if not self._embeddings.enabled:
                warnings.append("EMBEDDING_DISABLED")
            else:
                await self._progress(
                    job, DocumentStage.EMBEDDING, 65, "Indexing company knowledge."
                )
                values = await self._embeddings.embed_documents([chunk.text for chunk in chunks])
                if self._vectors.enabled:
                    await self._progress(
                        job,
                        DocumentStage.STORING_VECTORS,
                        85,
                        "Storing the private search index.",
                        {"embedded_chunk_count": len(values)},
                    )
                    records = [
                        VectorRecord(
                            id=chunk.vector_id or "",
                            values=value,
                            metadata={
                                "organization_id": organization_id,
                                "company_profile_id": job.company_profile_id,
                                "document_id": document.id,
                                "document_version": job.version,
                                "chunk_id": chunk.id,
                                "chunk_index": chunk.chunk_index,
                            },
                        )
                        for chunk, value in zip(chunks, values, strict=True)
                    ]
                    try:
                        count = await self._vectors.upsert(
                            namespace_for_organization(
                                self._settings.pinecone_namespace_prefix,
                                organization_id,
                            ),
                            records,
                        )
                        await self._progress(
                            job,
                            DocumentStage.STORING_VECTORS,
                            95,
                            "Finalizing company knowledge.",
                            {
                                "embedded_chunk_count": len(values),
                                "vector_count": count,
                            },
                        )
                    except VectorStoreError:
                        if self._settings.vector_search_required:
                            raise
                        warnings.append("VECTOR_SEARCH_DEGRADED")
                else:
                    warnings.append("VECTOR_SEARCH_DISABLED")

            await self._persistence.company_knowledge_chunks.activate_version(
                organization_id, document.id, job.version
            )
            await self._persistence.company_documents.update(
                organization_id,
                document.id,
                {
                    "status": DocumentStatus.READY,
                    "stage": DocumentStage.COMPLETED,
                    "progress_percent": 100,
                    "page_count": extracted.page_count,
                    "chunk_count": len(chunks),
                    "active_version": job.version,
                    "pending_version": None,
                    "warning_codes": list(dict.fromkeys(warnings)),
                    "error_code": None,
                    "error_message": None,
                    "completed_at": utc_now(),
                },
            )
            await self._persistence.document_ingestion_jobs.finalize(
                organization_id,
                job.id,
                DocumentJobStatus.SUCCEEDED,
                warning_codes=list(dict.fromkeys(warnings)),
            )
            await self._delete_old_vectors(document, job.version)
        except (DocumentProcessingError, EmbeddingError, VectorStoreError) as exc:
            await self._cleanup_pending_vectors(organization_id, pending_vector_ids)
            await self._persistence.company_knowledge_chunks.delete_version(
                organization_id, document.id, job.version
            )
            await self._fail(job, exc.code, exc.message, preserve_ready=document.active_version)
        except Exception as exc:
            logger.error(
                "document_ingestion_failed",
                organization_id=organization_id,
                document_id=document.id,
                ingestion_job_id=job.id,
                exception_type=type(exc).__name__,
            )
            await self._cleanup_pending_vectors(organization_id, pending_vector_ids)
            await self._persistence.company_knowledge_chunks.delete_version(
                organization_id, document.id, job.version
            )
            await self._fail(
                job,
                "DOCUMENT_PROCESSING_FAILED",
                "The PDF could not be processed.",
                preserve_ready=document.active_version,
            )

    async def _cleanup_pending_vectors(self, organization_id: str, vector_ids: list[str]) -> None:
        if not self._vectors.enabled or not vector_ids:
            return
        try:
            await self._vectors.delete(
                namespace_for_organization(
                    self._settings.pinecone_namespace_prefix, organization_id
                ),
                ids=vector_ids,
            )
        except VectorStoreError:
            logger.warning(
                "pending_document_vector_cleanup_failed",
                organization_id=organization_id,
                vector_count=len(vector_ids),
            )

    async def _delete_old_vectors(self, document: CompanyDocument, active_version: int) -> None:
        if not self._vectors.enabled or document.active_version is None:
            return
        old = await self._persistence.company_knowledge_chunks.list_for_document_version(
            document.organization_id, document.id, document.active_version
        )
        try:
            await self._vectors.delete(
                namespace_for_organization(
                    self._settings.pinecone_namespace_prefix,
                    document.organization_id,
                ),
                ids=[
                    chunk.vector_id
                    for chunk in old
                    if chunk.vector_id and chunk.document_version != active_version
                ],
            )
        except VectorStoreError:
            logger.warning(
                "stale_document_vector_cleanup_failed",
                organization_id=document.organization_id,
                document_id=document.id,
            )

    async def _progress(
        self,
        job: DocumentIngestionJob,
        stage: DocumentStage,
        percent: int,
        message: str,
        metrics: dict[str, int] | None = None,
    ) -> None:
        await self._persistence.document_ingestion_jobs.update_progress(
            job.organization_id, job.id, stage, percent, message, metrics
        )
        await self._persistence.company_documents.update(
            job.organization_id,
            job.document_id,
            {
                "status": DocumentStatus.PROCESSING,
                "stage": stage,
                "progress_percent": percent,
            },
        )

    async def _fail(
        self,
        job: DocumentIngestionJob,
        code: str,
        message: str,
        preserve_ready: int | None = None,
    ) -> None:
        await self._persistence.document_ingestion_jobs.finalize(
            job.organization_id,
            job.id,
            DocumentJobStatus.FAILED,
            error_code=code,
            error_message=message,
        )
        changes: dict[str, object] = {
            "pending_version": None,
            "error_code": code,
            "error_message": message,
        }
        if preserve_ready:
            changes.update(
                {
                    "status": DocumentStatus.READY,
                    "stage": DocumentStage.COMPLETED,
                    "warning_codes": ["DOCUMENT_REPROCESS_FAILED"],
                }
            )
        else:
            changes.update(
                {
                    "status": DocumentStatus.FAILED,
                    "stage": DocumentStage.FAILED,
                }
            )
        await self._persistence.company_documents.update(
            job.organization_id, job.document_id, changes
        )


class CompanyDocumentService:
    def __init__(
        self,
        *,
        persistence: Persistence,
        storage: ObjectStorage,
        executor: BackgroundExecutor,
        embeddings: EmbeddingProvider,
        vectors: VectorStore,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._storage = storage
        self._executor = executor
        self._embeddings = embeddings
        self._vectors = vectors
        self._settings = settings

    async def request_upload(
        self, context: AuthenticatedContext, request: UploadRequest
    ) -> UploadRequestResponse:
        if (
            request.content_type.casefold() != "application/pdf"
            or Path(request.filename).suffix.casefold() != ".pdf"
        ):
            raise self._error(
                "DOCUMENT_UNSUPPORTED_TYPE",
                "Only PDF documents are supported.",
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )
        if request.size_bytes > self._settings.document_max_file_size_bytes:
            raise self._error(
                "DOCUMENT_SIZE_LIMIT_EXCEEDED",
                "The PDF exceeds the 20 MB upload limit.",
                status.HTTP_413_CONTENT_TOO_LARGE,
            )
        existing = await self._persistence.company_documents.list_for_organization(
            context.organization_id
        )
        if len(existing) >= self._settings.document_max_files_per_company:
            raise self._error(
                "DOCUMENT_LIMIT_EXCEEDED",
                "This company has reached its document limit.",
                status.HTTP_409_CONFLICT,
            )
        now = utc_now()
        document_id = new_id()
        organization_digest = hashlib.sha256(context.organization_id.encode("utf-8")).hexdigest()[
            :16
        ]
        document = CompanyDocument(
            id=document_id,
            organization_id=context.organization_id,
            uploaded_by_user_id=context.user_id,
            original_filename=request.filename,
            content_type=request.content_type,
            storage_key=f"{organization_digest}/{document_id}.pdf",
            size_bytes=request.size_bytes,
            status=DocumentStatus.PENDING_UPLOAD,
            stage=DocumentStage.AWAITING_UPLOAD,
            created_at=now,
            updated_at=now,
        )
        await self._persistence.company_documents.create(document)
        try:
            presigned = await self._storage.create_upload(
                document.storage_key, document.content_type
            )
        except Exception as exc:
            await self._persistence.company_documents.update(
                context.organization_id,
                document.id,
                {
                    "status": DocumentStatus.FAILED,
                    "stage": DocumentStage.FAILED,
                    "error_code": "DOCUMENT_STORAGE_UNAVAILABLE",
                    "error_message": "Private document storage is temporarily unavailable.",
                },
            )
            raise self._error(
                "DOCUMENT_STORAGE_UNAVAILABLE",
                "Private document storage is temporarily unavailable.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        upload = (
            UploadTarget(
                method=presigned.method,
                url=presigned.url,
                headers=presigned.headers,
            )
            if presigned
            else UploadTarget(
                method="PUT",
                url=f"/api/v1/company-documents/{document.id}/content",
                headers={"Content-Type": "application/pdf"},
            )
        )
        return UploadRequestResponse(document=document, upload=upload)

    async def upload_content(
        self,
        context: AuthenticatedContext,
        document_id: str,
        chunks: AsyncIterator[bytes],
    ) -> CompanyDocument:
        document = await self._get(context.organization_id, document_id)
        if not self._storage.direct_uploads:
            raise self._error(
                "DOCUMENT_DIRECT_UPLOAD_DISABLED",
                "Use the provided upload URL for this storage backend.",
                status.HTTP_409_CONFLICT,
            )
        if document.status != DocumentStatus.PENDING_UPLOAD:
            raise self._error(
                "DOCUMENT_UPLOAD_STATE_INVALID",
                "This document is not awaiting content.",
                status.HTTP_409_CONFLICT,
            )
        try:
            stored = await self._storage.put_stream(
                document.storage_key,
                chunks,
                self._settings.document_max_file_size_bytes,
            )
        except StorageSizeExceededError as exc:
            raise self._error(
                "DOCUMENT_SIZE_LIMIT_EXCEEDED",
                "The PDF exceeds the 20 MB upload limit.",
                status.HTTP_413_CONTENT_TOO_LARGE,
            ) from exc
        except Exception as exc:
            raise self._error(
                "DOCUMENT_STORAGE_UNAVAILABLE",
                "Private document storage is temporarily unavailable.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        updated = await self._persistence.company_documents.update(
            context.organization_id,
            document.id,
            {"sha256": stored.sha256},
        )
        assert updated is not None
        return updated

    async def complete_upload(
        self,
        context: AuthenticatedContext,
        document_id: str,
        request: UploadCompleteRequest,
    ) -> CompanyDocument:
        document = await self._get(context.organization_id, document_id)
        if document.status != DocumentStatus.PENDING_UPLOAD:
            raise self._error(
                "DOCUMENT_UPLOAD_STATE_INVALID",
                "This document upload has already been completed.",
                status.HTTP_409_CONFLICT,
            )
        try:
            stored = await self._storage.stat(document.storage_key)
        except Exception as exc:
            raise self._error(
                "DOCUMENT_STORAGE_UNAVAILABLE",
                "Private document storage is temporarily unavailable.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        if stored is None:
            raise self._error(
                "DOCUMENT_UPLOAD_NOT_FOUND",
                "The uploaded PDF could not be found.",
                status.HTTP_409_CONFLICT,
            )
        actual_digest = stored.sha256
        if not actual_digest:
            try:
                actual_digest = hashlib.sha256(
                    await self._storage.read(document.storage_key)
                ).hexdigest()
            except Exception as exc:
                raise self._error(
                    "DOCUMENT_STORAGE_UNAVAILABLE",
                    "Private document storage is temporarily unavailable.",
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                ) from exc
        if (
            stored.size_bytes != request.size_bytes
            or request.size_bytes != document.size_bytes
            or actual_digest.casefold() != request.sha256.casefold()
        ):
            raise self._error(
                "DOCUMENT_UPLOAD_MISMATCH",
                "The uploaded PDF does not match the upload request.",
                status.HTTP_409_CONFLICT,
            )
        try:
            prefix = (await self._storage.read(document.storage_key))[:5]
        except Exception as exc:
            raise self._error(
                "DOCUMENT_STORAGE_UNAVAILABLE",
                "Private document storage is temporarily unavailable.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        if prefix != b"%PDF-":
            raise self._error(
                "DOCUMENT_INVALID_PDF",
                "The uploaded file is not a valid PDF.",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        updated = await self._persistence.company_documents.update(
            context.organization_id,
            document.id,
            {
                "sha256": actual_digest,
                "status": DocumentStatus.UPLOADED,
                "stage": DocumentStage.UPLOADED,
                "progress_percent": 0,
            },
        )
        assert updated is not None
        return updated

    async def attach_for_onboarding(
        self,
        *,
        context: AuthenticatedContext,
        company_profile_id: str,
        document_ids: list[str],
    ) -> list[DocumentIngestionJob]:
        if len(document_ids) > self._settings.document_onboarding_max_files:
            raise self._error(
                "DOCUMENT_ONBOARDING_LIMIT_EXCEEDED",
                "Only one PDF can be attached during onboarding.",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if len(set(document_ids)) != len(document_ids):
            raise self._error(
                "DOCUMENT_DUPLICATE_REFERENCE",
                "Document references must be unique.",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        documents = [
            await self._get(context.organization_id, document_id) for document_id in document_ids
        ]
        for document in documents:
            if document.company_profile_id not in {None, company_profile_id}:
                raise self._error(
                    "DOCUMENT_ALREADY_ATTACHED",
                    "The document is already attached to another company.",
                    status.HTTP_409_CONFLICT,
                )
            if document.status != DocumentStatus.UPLOADED:
                raise self._error(
                    "DOCUMENT_NOT_READY_FOR_INGESTION",
                    "Complete the document upload before onboarding.",
                    status.HTTP_409_CONFLICT,
                )
        return [await self._queue(context, document, company_profile_id) for document in documents]

    async def list_documents(self, context: AuthenticatedContext) -> list[CompanyDocument]:
        return await self._persistence.company_documents.list_for_organization(
            context.organization_id
        )

    async def get_document(
        self, context: AuthenticatedContext, document_id: str
    ) -> CompanyDocument:
        return await self._get(context.organization_id, document_id)

    async def get_job(self, context: AuthenticatedContext, job_id: str) -> DocumentIngestionJob:
        job = await self._persistence.document_ingestion_jobs.get_by_id(
            context.organization_id, job_id
        )
        if job is None:
            raise self._error(
                "DOCUMENT_JOB_NOT_FOUND",
                "The document ingestion job was not found.",
                status.HTTP_404_NOT_FOUND,
            )
        return job

    async def reprocess(
        self, context: AuthenticatedContext, document_id: str
    ) -> DocumentIngestionJob:
        company = await self._persistence.company_profiles.get_for_organization(
            context.organization_id
        )
        document = await self._get(context.organization_id, document_id)
        if company is None or document.company_profile_id not in {None, company.id}:
            raise self._not_found()
        if document.status == DocumentStatus.PROCESSING:
            raise self._error(
                "DOCUMENT_PROCESSING_ALREADY_RUNNING",
                "Document processing is already running.",
                status.HTTP_409_CONFLICT,
            )
        return await self._queue(context, document, company.id)

    async def delete(self, context: AuthenticatedContext, document_id: str) -> None:
        document = await self._get(context.organization_id, document_id)
        now = utc_now()
        await self._persistence.company_documents.update(
            context.organization_id,
            document.id,
            {
                "status": DocumentStatus.DELETED,
                "stage": DocumentStage.DELETED,
                "deleted_at": now,
            },
        )
        if self._vectors.enabled:
            try:
                await self._vectors.delete(
                    namespace_for_organization(
                        self._settings.pinecone_namespace_prefix,
                        context.organization_id,
                    ),
                    metadata_filter={
                        "organization_id": {"$eq": context.organization_id},
                        "document_id": {"$eq": document.id},
                    },
                )
            except VectorStoreError:
                logger.warning(
                    "document_vector_cleanup_deferred",
                    organization_id=context.organization_id,
                    document_id=document.id,
                )
        await self._persistence.company_knowledge_chunks.delete_for_document(
            context.organization_id, document.id
        )
        try:
            await self._storage.delete(document.storage_key)
        except Exception as exc:
            logger.warning(
                "document_object_cleanup_deferred",
                organization_id=context.organization_id,
                document_id=document.id,
                exception_type=type(exc).__name__,
            )

    async def search(
        self, context: AuthenticatedContext, request: KnowledgeSearchRequest
    ) -> KnowledgeSearchResponse:
        if not self._embeddings.enabled or not self._vectors.enabled:
            return KnowledgeSearchResponse(
                items=[],
                vector_search_enabled=False,
                warning_code="VECTOR_SEARCH_DISABLED",
            )
        top_k = min(
            request.top_k or self._settings.pinecone_default_top_k,
            self._settings.pinecone_max_top_k,
        )
        company = await self._persistence.company_profiles.get_for_organization(
            context.organization_id
        )
        if company is None:
            raise self._error(
                "COMPANY_PROFILE_NOT_FOUND",
                "No company profile exists for this organization.",
                status.HTTP_404_NOT_FOUND,
            )
        requested_ids = set(request.document_ids or [])
        documents = {
            item.id: item
            for item in await self._persistence.company_documents.list_for_organization(
                context.organization_id
            )
            if item.company_profile_id == company.id
            and item.active_version is not None
            and (not requested_ids or item.id in requested_ids)
        }
        if not documents:
            return KnowledgeSearchResponse(items=[], vector_search_enabled=True)
        vector = await self._embeddings.embed_query(request.query)
        metadata_filter: dict[str, object] = {
            "$and": [
                {"organization_id": {"$eq": context.organization_id}},
                {"company_profile_id": {"$eq": company.id}},
                {
                    "$or": [
                        {
                            "$and": [
                                {"document_id": {"$eq": document.id}},
                                {"document_version": {"$eq": document.active_version}},
                            ]
                        }
                        for document in documents.values()
                    ]
                },
            ]
        }
        matches = await self._vectors.query(
            namespace_for_organization(
                self._settings.pinecone_namespace_prefix,
                context.organization_id,
            ),
            vector,
            top_k=min(
                top_k * self._settings.pinecone_query_overfetch_factor,
                self._settings.pinecone_max_top_k,
            ),
            metadata_filter=metadata_filter,
        )
        chunks = await self._persistence.company_knowledge_chunks.get_many(
            context.organization_id,
            [
                str(match.metadata.get("chunk_id", ""))
                for match in matches
                if match.metadata.get("chunk_id")
            ],
        )
        by_id = {item.id: item for item in chunks}
        results: list[KnowledgeSearchResult] = []
        for match in matches:
            chunk = by_id.get(str(match.metadata.get("chunk_id", "")))
            document = documents.get(chunk.document_id) if chunk else None
            if (
                chunk is None
                or document is None
                or document.company_profile_id != chunk.company_profile_id
                or document.active_version != chunk.document_version
                or (request.document_ids and document.id not in requested_ids)
            ):
                continue
            results.append(
                KnowledgeSearchResult(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    filename=document.original_filename,
                    score=match.score,
                    excerpt=chunk.text[:1000],
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    heading=chunk.heading,
                )
            )
            if len(results) == top_k:
                break
        return KnowledgeSearchResponse(items=results, vector_search_enabled=True)

    async def _queue(
        self,
        context: AuthenticatedContext,
        document: CompanyDocument,
        company_profile_id: str,
    ) -> DocumentIngestionJob:
        version = (document.active_version or 0) + 1
        now = utc_now()
        job = DocumentIngestionJob(
            id=new_id(),
            organization_id=context.organization_id,
            company_profile_id=company_profile_id,
            document_id=document.id,
            requested_by_user_id=context.user_id,
            version=version,
            status=DocumentJobStatus.QUEUED,
            stage=DocumentStage.VALIDATING,
            progress_percent=0,
            message="Document ingestion is queued.",
            created_at=now,
            updated_at=now,
        )
        await self._persistence.document_ingestion_jobs.create(job)
        await self._persistence.company_documents.update(
            context.organization_id,
            document.id,
            {
                "company_profile_id": company_profile_id,
                "status": DocumentStatus.PROCESSING,
                "stage": DocumentStage.VALIDATING,
                "pending_version": version,
                "ingestion_job_id": job.id,
                "progress_percent": 0,
                "error_code": None,
                "error_message": None,
            },
        )
        self._executor.schedule(job_id=job.id, organization_id=context.organization_id)
        return job

    async def _get(self, organization_id: str, document_id: str) -> CompanyDocument:
        try:
            document = await self._persistence.company_documents.get_by_id(
                organization_id, document_id
            )
        except RepositoryUnavailableError as exc:
            raise self._error(
                "DATABASE_UNAVAILABLE",
                "The database is temporarily unavailable.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        if document is None:
            raise self._not_found()
        return document

    @staticmethod
    def _not_found() -> AppError:
        return CompanyDocumentService._error(
            "DOCUMENT_NOT_FOUND",
            "The requested document was not found.",
            status.HTTP_404_NOT_FOUND,
        )

    @staticmethod
    def _error(code: str, message: str, status_code: int) -> AppError:
        return AppError(code=code, message=message, status_code=status_code)
