import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta

from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.domain.background_jobs import BackgroundJob, BackgroundJobStatus, JobType
from app.domain.discovery import Competitor, CompetitorStatus
from app.domain.intelligence import NormalizedSection, PageType
from app.domain.intelligence_evidence import (
    CompetitorTimelineItem,
    CompetitorTimelinePage,
    CorrelationType,
    EvidenceBundle,
    EvidenceCoverage,
    EvidenceExcerpt,
    EvidenceKind,
    EvidenceSourceKind,
    EvidenceSourceType,
    IntelligenceAnswerRequest,
    IntelligenceAnswerResponse,
    IntelligenceCategory,
    IntelligenceCorrelation,
    IntelligenceEvidence,
    IntelligenceIndexingStatus,
    IntelligenceOverview,
    IntelligenceSearchRequest,
    IntelligenceSearchResponse,
    IntelligenceTrend,
)
from app.domain.onboarding import CompanyProfile, utc_now
from app.domain.scanning import CompetitorSnapshot
from app.domain.signals import CompetitorSignal, SignalType
from app.repositories.interfaces import Persistence, RepositoryConflictError
from app.services.company_documents import namespace_for_organization
from app.services.embeddings import EmbeddingError, EmbeddingProvider
from app.services.intelligence_synthesis import StructuredModelIntelligenceSynthesizer
from app.services.vector_store import VectorRecord, VectorStore, VectorStoreError

PROCESSING_VERSION = "competitor-evidence-v1"
_WHITESPACE = re.compile(r"\s+")


class CompetitorSnapshotChunker:
    """Create stable, heading-aware chunks from normalized competitor snapshots."""

    def __init__(
        self,
        *,
        target_characters: int,
        maximum_characters: int,
        overlap_characters: int,
        minimum_characters: int,
    ) -> None:
        if not 0 < minimum_characters <= target_characters <= maximum_characters:
            raise ValueError("Invalid competitor evidence chunk limits")
        if not 0 <= overlap_characters < target_characters:
            raise ValueError("Chunk overlap must be smaller than the target")
        self._target = target_characters
        self._maximum = maximum_characters
        self._overlap = overlap_characters
        self._minimum = minimum_characters

    def chunk(self, sections: Sequence[NormalizedSection]) -> list[tuple[str | None, str]]:
        chunks: list[tuple[str | None, str]] = []
        seen: set[str] = set()
        for section in sections:
            content = self._clean(section.content)
            if not content:
                continue
            pieces = self._split(content)
            for piece in pieces:
                text = self._clean(piece)
                digest = hashlib.sha256(text.casefold().encode()).hexdigest()
                if len(text) < self._minimum or digest in seen:
                    continue
                seen.add(digest)
                chunks.append((section.heading, text))
        return chunks

    def _split(self, content: str) -> list[str]:
        if len(content) <= self._maximum:
            return [content]
        paragraphs = [part.strip() for part in re.split(r"\n{2,}", content) if part.strip()]
        if len(paragraphs) == 1:
            paragraphs = re.split(r"(?<=[.!?])\s+", content)
        result: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if len(paragraph) > self._maximum:
                if current:
                    result.append(current)
                    current = ""
                result.extend(self._sliding_windows(paragraph))
                continue
            candidate = f"{current}\n\n{paragraph}".strip()
            if current and len(candidate) > self._target:
                result.append(current)
                overlap = current[-self._overlap :].lstrip() if self._overlap else ""
                current = f"{overlap}\n\n{paragraph}".strip()
            else:
                current = candidate
        if current:
            result.append(current)
        return result

    def _sliding_windows(self, content: str) -> list[str]:
        step = self._maximum - self._overlap
        return [
            content[start : start + self._maximum]
            for start in range(0, len(content), step)
            if len(content[start : start + self._maximum].strip()) >= self._minimum
        ]

    @staticmethod
    def _clean(value: str) -> str:
        lines = [_WHITESPACE.sub(" ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def category_for_page(page_type: PageType) -> IntelligenceCategory:
    return {
        PageType.PRICING: IntelligenceCategory.PRICING,
        PageType.FEATURES: IntelligenceCategory.FEATURES,
        PageType.PRODUCT: IntelligenceCategory.PRODUCT,
        PageType.SOLUTIONS: IntelligenceCategory.POSITIONING,
        PageType.ABOUT: IntelligenceCategory.POSITIONING,
        PageType.INTEGRATIONS: IntelligenceCategory.INTEGRATIONS,
        PageType.BLOG: IntelligenceCategory.CONTENT,
        PageType.CHANGELOG: IntelligenceCategory.PRODUCT,
        PageType.DOCUMENTATION: IntelligenceCategory.DOCUMENTATION,
        PageType.CUSTOMERS: IntelligenceCategory.CUSTOMER_PROOF,
    }.get(page_type, IntelligenceCategory.GENERAL)


def category_for_signal(signal_type: SignalType) -> IntelligenceCategory:
    return {
        SignalType.PRICING_CHANGE: IntelligenceCategory.PRICING,
        SignalType.FEATURE_LAUNCH: IntelligenceCategory.FEATURES,
        SignalType.FEATURE_REMOVAL: IntelligenceCategory.FEATURES,
        SignalType.POSITIONING_CHANGE: IntelligenceCategory.POSITIONING,
        SignalType.INTEGRATION_CHANGE: IntelligenceCategory.INTEGRATIONS,
        SignalType.CONTENT_CHANGE: IntelligenceCategory.CONTENT,
        SignalType.DOCUMENTATION_CHANGE: IntelligenceCategory.DOCUMENTATION,
        SignalType.CUSTOMER_PROOF_CHANGE: IntelligenceCategory.CUSTOMER_PROOF,
        SignalType.PRODUCT_CHANGE: IntelligenceCategory.PRODUCT,
        SignalType.CHANGELOG_UPDATE: IntelligenceCategory.PRODUCT,
    }.get(signal_type, IntelligenceCategory.GENERAL)


def source_type_for_signal(signal_type: SignalType) -> EvidenceSourceType:
    return {
        SignalType.PRICING_CHANGE: EvidenceSourceType.COMPETITOR_PRICING_SIGNAL,
        SignalType.FEATURE_LAUNCH: EvidenceSourceType.COMPETITOR_FEATURE_SIGNAL,
        SignalType.FEATURE_REMOVAL: EvidenceSourceType.COMPETITOR_FEATURE_SIGNAL,
        SignalType.POSITIONING_CHANGE: EvidenceSourceType.COMPETITOR_POSITIONING_SIGNAL,
        SignalType.INTEGRATION_CHANGE: EvidenceSourceType.COMPETITOR_INTEGRATION_SIGNAL,
        SignalType.CONTENT_CHANGE: EvidenceSourceType.COMPETITOR_CONTENT_SIGNAL,
    }.get(signal_type, EvidenceSourceType.COMPETITOR_SIGNAL)


class CompetitorEvidenceIndexingService:
    def __init__(
        self,
        *,
        persistence: Persistence,
        embeddings: EmbeddingProvider,
        vectors: VectorStore,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._embeddings = embeddings
        self._vectors = vectors
        self._settings = settings
        self._chunker = CompetitorSnapshotChunker(
            target_characters=settings.competitor_snapshot_chunk_target_characters,
            maximum_characters=settings.competitor_snapshot_chunk_max_characters,
            overlap_characters=settings.competitor_snapshot_chunk_overlap_characters,
            minimum_characters=settings.competitor_snapshot_chunk_min_characters,
        )

    async def index_snapshot(
        self,
        organization_id: str,
        snapshot_id: str,
        *,
        before_activation: Callable[[], Awaitable[bool]] | None = None,
    ) -> int:
        snapshot = await self._persistence.competitor_snapshots.get_by_id(
            organization_id, snapshot_id
        )
        if snapshot is None:
            raise AppError(
                code="INTELLIGENCE_EVIDENCE_NOT_FOUND",
                message="The source evidence no longer exists.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        profile, competitor = await self._validated_owner(
            organization_id,
            snapshot.competitor_id,
        )
        chunks = self._chunker.chunk(snapshot.sections)
        if not chunks:
            raise AppError(
                code="INTELLIGENCE_EVIDENCE_EMPTY",
                message="The source did not contain usable evidence.",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        evidence = [
            self._snapshot_evidence(
                profile.id,
                competitor.name,
                snapshot,
                index,
                heading,
                content,
            )
            for index, (heading, content) in enumerate(chunks)
        ]
        return await self._publish(evidence, before_activation=before_activation)

    async def index_signal(
        self,
        organization_id: str,
        signal_id: str,
        *,
        before_activation: Callable[[], Awaitable[bool]] | None = None,
    ) -> int:
        signal = await self._persistence.competitor_signals.get_by_id(organization_id, signal_id)
        if signal is None:
            raise AppError(
                code="INTELLIGENCE_EVIDENCE_NOT_FOUND",
                message="The source evidence no longer exists.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        profile, competitor = await self._validated_owner(
            organization_id,
            signal.competitor_id,
        )
        return await self._publish(
            [self._signal_evidence(profile.id, competitor.name, signal)],
            before_activation=before_activation,
        )

    async def _validated_owner(
        self,
        organization_id: str,
        competitor_id: str,
    ) -> tuple[CompanyProfile, Competitor]:
        profile = await self._persistence.company_profiles.get_for_organization(organization_id)
        if profile is None:
            raise AppError(
                code="COMPANY_PROFILE_NOT_FOUND",
                message="The workspace company profile was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        competitor = await self._persistence.competitors.get_by_id(
            organization_id,
            competitor_id,
        )
        if (
            competitor is None
            or competitor.company_profile_id != profile.id
            or competitor.status != CompetitorStatus.ACTIVE
        ):
            raise AppError(
                code="INTELLIGENCE_EVIDENCE_NOT_FOUND",
                message="The source evidence no longer belongs to an active competitor.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return profile, competitor

    async def reindex_organization(
        self,
        organization_id: str,
        *,
        competitor_ids: list[str] | None = None,
        should_continue: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, int]:
        indexed = 0
        failed = 0
        competitors = await self._persistence.competitors.list(organization_id, limit=500)
        allowed = set(competitor_ids or [])
        for competitor in competitors:
            if competitor.status != CompetitorStatus.ACTIVE or (
                allowed and competitor.id not in allowed
            ):
                continue
            snapshot_ids: list[str] = []
            snapshot_cursor: str | None = None
            while True:
                snapshots = await self._persistence.competitor_snapshots.list_for_competitor(
                    organization_id,
                    competitor.id,
                    cursor=snapshot_cursor,
                    limit=self._settings.competitor_evidence_database_batch_size,
                )
                snapshot_ids.extend(item.id for item in snapshots)
                if len(snapshots) < self._settings.competitor_evidence_database_batch_size:
                    break
                snapshot_cursor = snapshots[-1].id
            signal_ids: list[str] = []
            signal_cursor: str | None = None
            while True:
                signals = await self._persistence.competitor_signals.list_for_competitor(
                    organization_id,
                    competitor.id,
                    signal_type=None,
                    signal_status=None,
                    minimum_importance=0,
                    source_id=None,
                    observed_after=None,
                    observed_before=None,
                    scan_id=None,
                    cursor=signal_cursor,
                    limit=self._settings.competitor_evidence_database_batch_size,
                )
                signal_ids.extend(item.id for item in signals)
                if len(signals) < self._settings.competitor_evidence_database_batch_size:
                    break
                signal_cursor = signals[-1].id
            for snapshot_id in snapshot_ids:
                if should_continue is not None and not await should_continue():
                    raise AppError(
                        code="INTELLIGENCE_INDEXING_CANCELLED",
                        message="Workspace evidence reindexing was cancelled.",
                        status_code=status.HTTP_409_CONFLICT,
                    )
                try:
                    indexed += await self.index_snapshot(organization_id, snapshot_id)
                except (AppError, EmbeddingError, VectorStoreError):
                    failed += 1
            for signal_id in signal_ids:
                if should_continue is not None and not await should_continue():
                    raise AppError(
                        code="INTELLIGENCE_INDEXING_CANCELLED",
                        message="Workspace evidence reindexing was cancelled.",
                        status_code=status.HTTP_409_CONFLICT,
                    )
                try:
                    indexed += await self.index_signal(organization_id, signal_id)
                except (AppError, EmbeddingError, VectorStoreError):
                    failed += 1
        return {"indexed_count": indexed, "failed_count": failed}

    async def cleanup_source(
        self,
        organization_id: str,
        source_kind: EvidenceSourceKind,
        source_reference_id: str,
    ) -> int:
        vector_ids = await self._persistence.intelligence_evidence.deactivate_source(
            organization_id, source_kind, source_reference_id
        )
        if vector_ids and self._vectors.enabled:
            await self._vectors.delete(
                namespace_for_organization(
                    self._settings.pinecone_namespace_prefix, organization_id
                ),
                ids=vector_ids,
            )
        return len(vector_ids)

    async def _publish(
        self,
        evidence: list[IntelligenceEvidence],
        *,
        before_activation: Callable[[], Awaitable[bool]] | None = None,
    ) -> int:
        if not self._settings.competitor_evidence_indexing_enabled:
            return 0
        if not self._embeddings.enabled or not self._vectors.enabled:
            raise AppError(
                code="INTELLIGENCE_PROVIDER_UNAVAILABLE",
                message="Competitor intelligence indexing is not available.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        first = evidence[0]
        source_reference_id = (
            first.snapshot_id
            if first.source_kind == EvidenceSourceKind.SNAPSHOT
            else first.signal_id
        )
        assert source_reference_id is not None
        existing = await self._persistence.intelligence_evidence.list_for_version(
            first.organization_id, first.source_kind, source_reference_id, first.version_key
        )
        records = existing or evidence
        if not existing:
            try:
                await self._persistence.intelligence_evidence.create_many(records)
            except RepositoryConflictError:
                records = await self._persistence.intelligence_evidence.list_for_version(
                    first.organization_id,
                    first.source_kind,
                    source_reference_id,
                    first.version_key,
                )
        texts = [self._embedding_text(item) for item in records]
        try:
            vectors = await self._embeddings.embed_documents(texts)
            if len(vectors) != len(records):
                raise EmbeddingError(
                    "EMBEDDING_PROVIDER_INVALID_RESPONSE",
                    "The embedding provider returned an invalid response.",
                )
            acknowledged = await self._vectors.upsert(
                namespace_for_organization(
                    self._settings.pinecone_namespace_prefix, first.organization_id
                ),
                [
                    VectorRecord(
                        id=item.vector_id,
                        values=vector,
                        metadata=self._metadata(item),
                    )
                    for item, vector in zip(records, vectors, strict=True)
                ],
            )
            if acknowledged != len(records):
                raise VectorStoreError("VECTOR_UPSERT_PARTIAL", "Vector indexing was incomplete.")
        except (EmbeddingError, VectorStoreError):
            if not existing:
                await self._persistence.intelligence_evidence.delete_version(
                    first.organization_id,
                    first.source_kind,
                    source_reference_id,
                    first.version_key,
                )
            raise
        if before_activation is not None and not await before_activation():
            raise AppError(
                code="INTELLIGENCE_INDEXING_CANCELLED",
                message="Evidence indexing stopped before version activation.",
                status_code=status.HTTP_409_CONFLICT,
            )
        prior = await self._persistence.intelligence_evidence.list_for_source(
            first.organization_id,
            first.source_kind,
            source_reference_id,
            active_only=True,
        )
        prior_ids = [
            item.vector_id
            for item in prior
            if (
                item.source_kind == first.source_kind
                and (item.snapshot_id == first.snapshot_id or item.signal_id == first.signal_id)
                and item.version_key != first.version_key
            )
        ]
        await self._persistence.intelligence_evidence.activate_version(
            first.organization_id, first.source_kind, source_reference_id, first.version_key
        )
        if prior_ids:
            await self._vectors.delete(
                namespace_for_organization(
                    self._settings.pinecone_namespace_prefix, first.organization_id
                ),
                ids=prior_ids,
            )
        return len(records)

    def _snapshot_evidence(
        self,
        company_profile_id: str,
        competitor_name: str,
        snapshot: CompetitorSnapshot,
        index: int,
        heading: str | None,
        content: str,
    ) -> IntelligenceEvidence:
        version_key = (
            f"{EvidenceSourceType.COMPETITOR_WEBSITE_SNAPSHOT.value}:"
            f"{snapshot.id}:{PROCESSING_VERSION}:{snapshot.content_hash}"
        )
        evidence_id = self._stable_id(
            snapshot.organization_id, "snapshot", snapshot.id, version_key, str(index)
        )
        now = utc_now()
        return IntelligenceEvidence(
            id=evidence_id,
            organization_id=snapshot.organization_id,
            company_profile_id=company_profile_id,
            competitor_id=snapshot.competitor_id,
            source_id=snapshot.source_id,
            snapshot_id=snapshot.id,
            chunk_index=index,
            source_type=EvidenceSourceType.COMPETITOR_WEBSITE_SNAPSHOT,
            source_kind=EvidenceSourceKind.SNAPSHOT,
            evidence_kind=EvidenceKind.SNAPSHOT_CHUNK,
            category=category_for_page(snapshot.page_type),
            source_name=competitor_name,
            title=snapshot.title or heading or snapshot.canonical_url,
            section_heading=heading,
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            processing_version=PROCESSING_VERSION,
            version_key=version_key,
            source_url=snapshot.final_url,
            observed_at=snapshot.fetched_at,
            created_at=now,
            updated_at=now,
            vector_id=f"iev_{evidence_id}",
            embedding_provider=self._settings.embedding_provider,
            embedding_model=self._settings.embedding_model,
            embedding_version=self._embeddings.version,
            embedding_dimensions=self._embeddings.dimensions,
            metadata={"page_type": snapshot.page_type.value},
        )

    def _signal_evidence(
        self,
        company_profile_id: str,
        competitor_name: str,
        signal: CompetitorSignal,
    ) -> IntelligenceEvidence:
        parts = [signal.summary, *signal.observed_facts]
        if signal.before_excerpt:
            parts.append(f"Before: {signal.before_excerpt}")
        parts.append(f"After: {signal.after_excerpt}")
        content = "\n".join(dict.fromkeys(part.strip() for part in parts if part.strip()))
        digest = hashlib.sha256(content.encode()).hexdigest()
        version_key = (
            f"{EvidenceSourceType.COMPETITOR_SIGNAL.value}:"
            f"{signal.id}:{PROCESSING_VERSION}:{digest}"
        )
        evidence_id = self._stable_id(signal.organization_id, "signal", signal.id, version_key, "0")
        return IntelligenceEvidence(
            id=evidence_id,
            organization_id=signal.organization_id,
            company_profile_id=company_profile_id,
            competitor_id=signal.competitor_id,
            source_id=signal.source_id,
            signal_id=signal.id,
            snapshot_id=signal.current_snapshot_id,
            chunk_index=0,
            source_type=source_type_for_signal(signal.type),
            source_kind=EvidenceSourceKind.SIGNAL,
            evidence_kind=EvidenceKind.SIGNAL_SUMMARY,
            category=category_for_signal(signal.type),
            source_name=competitor_name,
            title=signal.title,
            content=content,
            content_hash=digest,
            processing_version=PROCESSING_VERSION,
            version_key=version_key,
            source_url=signal.source_url,
            observed_at=signal.observed_at,
            created_at=signal.created_at,
            updated_at=utc_now(),
            vector_id=f"iev_{evidence_id}",
            embedding_provider=self._settings.embedding_provider,
            embedding_model=self._settings.embedding_model,
            embedding_version=self._embeddings.version,
            embedding_dimensions=self._embeddings.dimensions,
            metadata={"signal_type": signal.type.value},
        )

    @staticmethod
    def _stable_id(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]

    @staticmethod
    def _embedding_text(item: IntelligenceEvidence) -> str:
        heading = f"\nSection: {item.section_heading}" if item.section_heading else ""
        source = f"Source: {item.source_name}\n" if item.source_name else ""
        return (
            f"{source}Title: {item.title}{heading}\nCategory: {item.category.value}\n{item.content}"
        )

    @staticmethod
    def _metadata(item: IntelligenceEvidence) -> dict[str, str | int | bool]:
        return {
            "organization_id": item.organization_id,
            "company_profile_id": item.company_profile_id,
            "competitor_id": item.competitor_id or "",
            "evidence_id": item.id,
            "source_type": item.source_type.value,
            "source_kind": item.source_kind.value,
            "evidence_kind": item.evidence_kind.value,
            "source_id": item.source_id or "",
            "snapshot_id": item.snapshot_id or "",
            "signal_id": item.signal_id or "",
            "category": item.category.value,
            "evidence_version_key": item.version_key,
            "observed_at_epoch": int(item.observed_at.timestamp()),
            "active": True,
            "deleted": False,
        }


class IntelligenceSearchService:
    def __init__(
        self,
        *,
        persistence: Persistence,
        embeddings: EmbeddingProvider,
        vectors: VectorStore,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._embeddings = embeddings
        self._vectors = vectors
        self._settings = settings

    async def search(
        self, context: AuthenticatedContext, request: IntelligenceSearchRequest
    ) -> IntelligenceSearchResponse:
        self._validate_request(request)
        await self._validate_competitors(context.organization_id, request.competitor_ids)
        profile = await self._persistence.company_profiles.get_for_organization(
            context.organization_id
        )
        if profile is None:
            raise AppError(
                code="COMPANY_PROFILE_NOT_FOUND",
                message="The workspace company profile was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if not self._settings.competitor_evidence_indexing_enabled:
            raise AppError(
                code="INTELLIGENCE_SEARCH_DISABLED",
                message="Competitor intelligence search is disabled.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not self._embeddings.enabled or not self._vectors.enabled:
            raise self._unavailable()
        try:
            vector = await self._embeddings.embed_query(request.query)
            matches = await self._vectors.query(
                namespace_for_organization(
                    self._settings.pinecone_namespace_prefix, context.organization_id
                ),
                vector,
                top_k=min(
                    self._settings.pinecone_max_top_k,
                    request.limit * self._settings.pinecone_query_overfetch_factor,
                ),
                metadata_filter=self._metadata_filter(context.organization_id, profile.id, request),
            )
        except VectorStoreError as exc:
            code = (
                "INTELLIGENCE_VECTOR_VERSION_MISMATCH"
                if exc.code == "VECTOR_INDEX_DIMENSION_MISMATCH"
                else "INTELLIGENCE_PROVIDER_UNAVAILABLE"
            )
            raise AppError(
                code=code,
                message="Competitor intelligence retrieval is temporarily unavailable.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        except EmbeddingError as exc:
            raise AppError(
                code="INTELLIGENCE_PROVIDER_UNAVAILABLE",
                message="Competitor intelligence retrieval is temporarily unavailable.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        evidence_ids = [
            str(match.metadata.get("evidence_id", ""))
            for match in matches
            if match.metadata.get("evidence_id")
        ]
        canonical = await self._persistence.intelligence_evidence.get_many(
            context.organization_id, evidence_ids
        )
        by_id = {item.id: item for item in canonical}
        chunk_ids = [
            str(match.metadata.get("chunk_id", ""))
            for match in matches
            if request.include_company_context and match.metadata.get("chunk_id")
        ]
        company_chunks = await self._persistence.company_knowledge_chunks.get_many(
            context.organization_id, chunk_ids
        )
        chunks_by_id = {
            item.id: item
            for item in company_chunks
            if item.company_profile_id == profile.id and item.active
        }
        competitor_names: dict[str, str] = {}
        for competitor_id in {item.competitor_id for item in canonical if item.competitor_id}:
            competitor = await self._persistence.competitors.get_by_id(
                context.organization_id, competitor_id
            )
            if competitor is not None and competitor.company_profile_id == profile.id:
                competitor_names[competitor.id] = competitor.name
        excerpts: list[EvidenceExcerpt] = []
        seen: set[tuple[str | None, str]] = set()
        for match in matches:
            evidence_id = str(match.metadata.get("evidence_id", ""))
            item = by_id.get(evidence_id)
            if (
                item is not None
                and item.company_profile_id == profile.id
                and item.competitor_id is not None
                and item.competitor_id in competitor_names
                and self._matches_request(item, request)
            ):
                overlap_key = (item.source_id, item.content_hash)
                if overlap_key in seen:
                    continue
                seen.add(overlap_key)
                excerpts.append(
                    self._excerpt(
                        item,
                        match.score,
                        competitor_names.get(item.competitor_id or ""),
                    )
                )
            elif request.include_company_context:
                chunk_id = str(match.metadata.get("chunk_id", ""))
                chunk = chunks_by_id.get(chunk_id)
                if chunk is None:
                    continue
                document = await self._persistence.company_documents.get_by_id(
                    context.organization_id, chunk.document_id
                )
                if (
                    document is None
                    or document.company_profile_id != profile.id
                    or document.active_version != chunk.document_version
                ):
                    continue
                overlap_key = (chunk.document_id, chunk.text_hash)
                if overlap_key in seen:
                    continue
                seen.add(overlap_key)
                excerpts.append(
                    EvidenceExcerpt(
                        evidence_id=f"company-chunk:{chunk.id}",
                        competitor_id=None,
                        source_type=EvidenceSourceType.COMPANY_DOCUMENT,
                        source_kind=EvidenceSourceKind.DOCUMENT,
                        evidence_kind=EvidenceKind.DOCUMENT_CHUNK_REFERENCE,
                        signal_category=IntelligenceCategory.DOCUMENTATION,
                        title=document.original_filename,
                        section_title=chunk.heading,
                        content_excerpt=self._safe_excerpt(chunk.text),
                        source_url=None,
                        observed_at=document.completed_at or document.updated_at,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        score=max(-1, min(1, match.score)),
                    )
                )
            else:
                continue
            if len(excerpts) >= request.limit:
                break
        warnings: list[str] = []
        if not excerpts:
            warnings.append("No active evidence matched the current query and filters.")
        return IntelligenceSearchResponse(
            query=request.query, bundle=self._bundle(excerpts, warnings)
        )

    async def answer(
        self, context: AuthenticatedContext, request: IntelligenceAnswerRequest
    ) -> IntelligenceAnswerResponse:
        search = await self.search(context, IntelligenceSearchRequest(**request.model_dump()))
        profile = await self._persistence.company_profiles.get_for_organization(
            context.organization_id
        )
        canonical = await self._persistence.intelligence_evidence.get_many(
            context.organization_id,
            [
                item.evidence_id
                for item in search.bundle.items
                if not item.evidence_id.startswith("company-chunk:")
            ],
        )
        synthesis = await StructuredModelIntelligenceSynthesizer(
            enabled=self._settings.intelligence_structured_synthesis_enabled,
            model=None,
        ).synthesize(
            query=request.query,
            company_context=(
                {
                    "company_name": profile.company_name,
                    "description": profile.one_line_description,
                }
                if profile
                else {}
            ),
            evidence_bundle=search.bundle,
            correlations=self._correlations(canonical),
            trends=await self.trends(
                context,
                window_days=self._settings.intelligence_default_lookback_days,
            ),
        )
        return IntelligenceAnswerResponse(
            answer=synthesis.answer,
            observations=synthesis.observations,
            inferences=synthesis.inferences,
            correlations=synthesis.correlations,
            historical_patterns=synthesis.historical_patterns,
            uncertainties=synthesis.uncertainties,
            evidence_ids=synthesis.evidence_ids,
            competitor_ids=synthesis.competitor_ids,
            date_range=synthesis.date_range,
            source_coverage=synthesis.source_coverage,
            citations=synthesis.citations,
            bundle=search.bundle,
            synthesis_mode=synthesis.mode,
        )

    async def timeline(
        self,
        context: AuthenticatedContext,
        competitor_id: str,
        *,
        cursor: str | None,
        limit: int,
        categories: list[IntelligenceCategory] | None,
        source_types: list[EvidenceSourceType] | None,
        observed_after: datetime | None,
        observed_before: datetime | None,
        ascending: bool,
    ) -> CompetitorTimelinePage:
        await self._validate_competitors(context.organization_id, [competitor_id])
        items = await self._persistence.intelligence_evidence.list_active(
            context.organization_id,
            competitor_id=competitor_id,
            categories=categories,
            source_types=source_types,
            observed_after=observed_after,
            observed_before=observed_before,
            limit=1000,
        )
        items.sort(key=lambda item: (item.observed_at, item.id), reverse=not ascending)
        unique_items: list[IntelligenceEvidence] = []
        seen_events: set[str] = set()
        for item in items:
            event_key = item.signal_id or item.snapshot_id or item.id
            if event_key in seen_events:
                continue
            seen_events.add(event_key)
            unique_items.append(item)
        items = unique_items
        if cursor:
            index = next((i for i, item in enumerate(items) if item.id == cursor), None)
            if index is not None:
                items = items[index + 1 :]
        page = items[:limit]
        timeline_items: list[CompetitorTimelineItem] = []
        for item in page:
            before = after = None
            if item.source_kind == EvidenceSourceKind.SIGNAL:
                signal = (
                    await self._persistence.competitor_signals.get_by_id(
                        context.organization_id, item.signal_id or ""
                    )
                    if item.signal_id
                    else None
                )
                if signal:
                    before, after = signal.before_excerpt, signal.after_excerpt
            timeline_items.append(
                CompetitorTimelineItem(
                    evidence_id=item.id,
                    source_type=item.source_type,
                    category=item.category,
                    title=item.title,
                    excerpt=self._safe_excerpt(item.content),
                    before_excerpt=before,
                    after_excerpt=after,
                    source_url=item.source_url,
                    observed_at=item.observed_at,
                )
            )
        return CompetitorTimelinePage(
            items=timeline_items,
            next_cursor=page[-1].id if len(items) > limit and page else None,
        )

    async def trends(
        self, context: AuthenticatedContext, *, window_days: int
    ) -> list[IntelligenceTrend]:
        cutoff = utc_now() - timedelta(days=window_days)
        evidence = await self._persistence.intelligence_evidence.list_active(
            context.organization_id, observed_after=cutoff, limit=5000
        )
        unique_events: dict[str, IntelligenceEvidence] = {}
        for item in evidence:
            event_key = item.signal_id or item.snapshot_id or item.id
            unique_events.setdefault(event_key, item)
        grouped: dict[IntelligenceCategory, list[IntelligenceEvidence]] = defaultdict(list)
        for item in unique_events.values():
            grouped[item.category].append(item)
        now = utc_now()
        return [
            IntelligenceTrend(
                window_days=window_days,
                category=category,
                event_count=len(items),
                competitor_count=len({item.competitor_id for item in items if item.competitor_id}),
                direction="increasing" if len(items) >= 3 else "steady",
                evidence_ids=[item.id for item in items[:10]],
                competitor_counts=dict(
                    Counter(item.competitor_id for item in items if item.competitor_id)
                ),
                pricing_increase_count=sum(
                    self._pricing_direction(item.content) == "increase" for item in items
                ),
                pricing_decrease_count=sum(
                    self._pricing_direction(item.content) == "decrease" for item in items
                ),
                scan_coverage=len({item.snapshot_id for item in items if item.snapshot_id}),
                freshest_evidence_age_days=min(
                    ((now - item.observed_at).days for item in items),
                    default=None,
                ),
            )
            for category, items in sorted(grouped.items(), key=lambda pair: pair[0].value)
        ]

    async def overview(self, context: AuthenticatedContext) -> IntelligenceOverview:
        recent = await self._persistence.intelligence_evidence.list_active(
            context.organization_id,
            observed_after=utc_now()
            - timedelta(days=self._settings.intelligence_default_lookback_days),
            limit=self._settings.intelligence_max_evidence_bundle_items,
        )
        recent.sort(key=lambda item: (item.observed_at, item.id), reverse=True)
        recent_events: list[IntelligenceEvidence] = []
        seen_events: set[str] = set()
        for item in recent:
            event_key = item.signal_id or item.snapshot_id or item.id
            if event_key in seen_events:
                continue
            seen_events.add(event_key)
            recent_events.append(item)
        timeline = [
            CompetitorTimelineItem(
                evidence_id=item.id,
                source_type=item.source_type,
                category=item.category,
                title=item.title,
                excerpt=self._safe_excerpt(item.content),
                source_url=item.source_url,
                observed_at=item.observed_at,
            )
            for item in recent_events
        ]
        correlations = self._correlations(recent_events)
        trends = await self.trends(
            context, window_days=self._settings.intelligence_default_lookback_days
        )
        active, _, _ = await self._persistence.intelligence_evidence.count_status(
            context.organization_id
        )
        return IntelligenceOverview(
            recent_changes=timeline,
            correlations=correlations,
            trends=trends,
            warnings=[] if active else ["No competitor evidence has been indexed yet."],
        )

    async def indexing_status(self, context: AuthenticatedContext) -> IntelligenceIndexingStatus:
        active, inactive, latest = await self._persistence.intelligence_evidence.count_status(
            context.organization_id
        )
        failed_jobs = await self._persistence.background_jobs.list(
            context.organization_id,
            status=BackgroundJobStatus.FAILED,
            cursor=None,
            limit=100,
        )
        dead_jobs = await self._persistence.background_jobs.list(
            context.organization_id,
            status=BackgroundJobStatus.DEAD_LETTERED,
            cursor=None,
            limit=100,
        )
        pending_jobs: list[BackgroundJob] = []
        for job_status in (
            BackgroundJobStatus.QUEUED,
            BackgroundJobStatus.CLAIMED,
            BackgroundJobStatus.RUNNING,
            BackgroundJobStatus.RETRY_SCHEDULED,
        ):
            pending_jobs.extend(
                await self._persistence.background_jobs.list(
                    context.organization_id,
                    status=job_status,
                    cursor=None,
                    limit=100,
                )
            )
        intelligence_job_types = {
            JobType.COMPETITOR_SNAPSHOT_INDEXING,
            JobType.COMPETITOR_SIGNAL_INDEXING,
            JobType.COMPETITOR_INTELLIGENCE_REINDEX,
        }
        jobs = [job for job in [*failed_jobs, *dead_jobs] if job.job_type in intelligence_job_types]
        pending = [job for job in pending_jobs if job.job_type in intelligence_job_types]
        active_evidence = await self._persistence.intelligence_evidence.list_active(
            context.organization_id,
            limit=5000,
        )
        active_versions: dict[tuple[EvidenceSourceKind, str], set[str]] = defaultdict(set)
        for item in active_evidence:
            source_reference = item.snapshot_id or item.signal_id or item.document_id or item.id
            active_versions[(item.source_kind, source_reference)].add(item.version_key)
        active_version_consistent = all(
            len(version_keys) == 1 for version_keys in active_versions.values()
        )
        now = utc_now()
        oldest_pending_age = max(
            ((now - job.created_at).total_seconds() for job in pending),
            default=None,
        )
        available = (
            self._settings.competitor_evidence_indexing_enabled
            and self._embeddings.enabled
            and self._vectors.enabled
        )
        warnings = []
        if not available:
            warnings.append("Semantic intelligence search is currently unavailable.")
        if jobs:
            warnings.append("Some evidence indexing jobs require attention.")
        return IntelligenceIndexingStatus(
            active_evidence_count=active,
            inactive_evidence_count=inactive,
            pending_job_count=len(pending),
            failed_job_count=len(jobs),
            oldest_pending_job_age_seconds=(
                max(0, int(oldest_pending_age)) if oldest_pending_age is not None else None
            ),
            active_version_consistent=active_version_consistent,
            indexing_available=available,
            last_indexed_at=latest,
            warnings=warnings,
        )

    async def _validate_competitors(self, organization_id: str, competitor_ids: list[str]) -> None:
        for competitor_id in competitor_ids:
            competitor = await self._persistence.competitors.get_by_id(
                organization_id, competitor_id
            )
            if competitor is None or competitor.status != CompetitorStatus.ACTIVE:
                raise AppError(
                    code="INTELLIGENCE_FILTER_INVALID",
                    message="One or more competitor filters are invalid.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

    def _validate_request(self, request: IntelligenceSearchRequest) -> None:
        if len(request.query) < 2:
            raise AppError(
                code="INTELLIGENCE_QUERY_INVALID",
                message="Enter a more specific intelligence query.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if any(unicodedata.category(character) == "Cc" for character in request.query):
            raise AppError(
                code="INTELLIGENCE_QUERY_INVALID",
                message="The intelligence query contains unsupported control characters.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if len(request.query) > self._settings.intelligence_search_query_max_characters:
            raise AppError(
                code="INTELLIGENCE_QUERY_TOO_LONG",
                message="The intelligence query is too long.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if len(request.competitor_ids) > self._settings.intelligence_max_competitors_per_query:
            raise AppError(
                code="INTELLIGENCE_TOO_MANY_COMPETITORS",
                message="The query includes too many competitors.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if request.limit > self._settings.intelligence_search_max_limit:
            raise AppError(
                code="INTELLIGENCE_QUERY_INVALID",
                message="The requested evidence limit is too large.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        dates = [item for item in (request.observed_after, request.observed_before) if item]
        if any(item.tzinfo is None for item in dates):
            raise AppError(
                code="INTELLIGENCE_DATE_RANGE_INVALID",
                message="Intelligence dates must include a timezone.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if (
            request.observed_after is not None
            and request.observed_before is not None
            and request.observed_after > request.observed_before
        ):
            raise AppError(
                code="INTELLIGENCE_DATE_RANGE_INVALID",
                message="The intelligence date range is invalid.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        oldest = utc_now() - timedelta(days=self._settings.intelligence_max_lookback_days)
        if request.observed_after is not None and request.observed_after < oldest:
            raise AppError(
                code="INTELLIGENCE_DATE_RANGE_INVALID",
                message="The intelligence date range exceeds the configured lookback.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    @staticmethod
    def _metadata_filter(
        organization_id: str,
        company_profile_id: str,
        request: IntelligenceSearchRequest,
    ) -> dict[str, object]:
        clauses: list[dict[str, object]] = [
            {"organization_id": {"$eq": organization_id}},
            {"company_profile_id": {"$eq": company_profile_id}},
        ]
        competitor_clauses: list[dict[str, object]] = [
            {"active": {"$eq": True}},
            {"deleted": {"$eq": False}},
            {"evidence_version_key": {"$ne": ""}},
        ]
        if request.competitor_ids:
            competitor_clauses.append({"competitor_id": {"$in": request.competitor_ids}})
        if request.categories:
            competitor_clauses.append(
                {"category": {"$in": [item.value for item in request.categories]}}
            )
        if request.source_types:
            competitor_clauses.append(
                {"source_type": {"$in": [item.value for item in request.source_types]}}
            )
        if request.evidence_kinds:
            competitor_clauses.append(
                {"evidence_kind": {"$in": [item.value for item in request.evidence_kinds]}}
            )
        date_filter: dict[str, int] = {}
        if request.observed_after:
            date_filter["$gte"] = int(request.observed_after.timestamp())
        if request.observed_before:
            date_filter["$lte"] = int(request.observed_before.timestamp())
        if date_filter:
            competitor_clauses.append({"observed_at_epoch": date_filter})
        include_documents = (
            request.include_company_context
            and (
                not request.source_types
                or EvidenceSourceType.COMPANY_DOCUMENT in request.source_types
            )
            and (
                not request.evidence_kinds
                or EvidenceKind.DOCUMENT_CHUNK_REFERENCE in request.evidence_kinds
            )
        )
        if include_documents:
            clauses.append(
                {
                    "$or": [
                        {"$and": competitor_clauses},
                        {"document_version": {"$gte": 1}},
                    ]
                }
            )
        else:
            clauses.extend(competitor_clauses)
        return {"$and": clauses}

    @staticmethod
    def _matches_request(item: IntelligenceEvidence, request: IntelligenceSearchRequest) -> bool:
        return (
            (not request.competitor_ids or item.competitor_id in request.competitor_ids)
            and (not request.categories or item.category in request.categories)
            and (not request.source_types or item.source_type in request.source_types)
            and (not request.evidence_kinds or item.evidence_kind in request.evidence_kinds)
            and (request.observed_after is None or item.observed_at >= request.observed_after)
            and (request.observed_before is None or item.observed_at <= request.observed_before)
        )

    @classmethod
    def _excerpt(
        cls,
        item: IntelligenceEvidence,
        score: float,
        competitor_name: str | None,
    ) -> EvidenceExcerpt:
        return EvidenceExcerpt(
            evidence_id=item.id,
            competitor_id=item.competitor_id,
            competitor_name=competitor_name,
            source_type=item.source_type,
            source_kind=item.source_kind,
            evidence_kind=item.evidence_kind,
            signal_category=item.category,
            title=item.title,
            section_title=item.section_heading,
            content_excerpt=cls._safe_excerpt(item.content),
            source_url=item.source_url,
            observed_at=item.observed_at,
            snapshot_id=item.snapshot_id,
            signal_id=item.signal_id,
            page_start=item.page_number_start,
            page_end=item.page_number_end,
            score=max(-1, min(1, score)),
        )

    @staticmethod
    def _safe_excerpt(content: str, maximum: int = 700) -> str:
        clean = _WHITESPACE.sub(" ", content).strip()
        return clean if len(clean) <= maximum else f"{clean[: maximum - 1].rstrip()}…"

    @staticmethod
    def _pricing_direction(content: str) -> str | None:
        before = re.search(r"Before:[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)", content)
        after = re.search(r"After:[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)", content)
        if before is None or after is None:
            return None
        before_value = float(before.group(1))
        after_value = float(after.group(1))
        if after_value > before_value:
            return "increase"
        if after_value < before_value:
            return "decrease"
        return None

    @staticmethod
    def _bundle(items: list[EvidenceExcerpt], warnings: list[str]) -> EvidenceBundle:
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in items:
            grouped[item.competitor_id or "company"].append(item.evidence_id)
        dates = [item.observed_at for item in items]
        return EvidenceBundle(
            items=items,
            grouped_by_competitor=dict(grouped),
            coverage=EvidenceCoverage(
                competitor_count=len({item.competitor_id for item in items if item.competitor_id}),
                source_type_count=len({item.source_type for item in items}),
                category_count=len({item.signal_category for item in items}),
                earliest_observed_at=min(dates) if dates else None,
                latest_observed_at=max(dates) if dates else None,
            ),
            warnings=warnings,
        )

    @staticmethod
    def _correlations(
        items: list[IntelligenceEvidence],
    ) -> list[IntelligenceCorrelation]:
        correlations: list[IntelligenceCorrelation] = []
        unique_events: dict[tuple[str, str], IntelligenceEvidence] = {}
        for item in items:
            event_key = item.signal_id or item.snapshot_id or item.id
            unique_events.setdefault((item.organization_id, event_key), item)
        by_competitor: dict[tuple[str, str], list[IntelligenceEvidence]] = defaultdict(list)
        by_category: dict[tuple[str, IntelligenceCategory], list[IntelligenceEvidence]] = (
            defaultdict(list)
        )
        for item in unique_events.values():
            if item.competitor_id:
                by_competitor[(item.organization_id, item.competitor_id)].append(item)
            by_category[(item.organization_id, item.category)].append(item)
        for (_, competitor_id), evidence in sorted(by_competitor.items()):
            source_types = {item.source_type for item in evidence}
            if len(source_types) >= 2:
                correlations.append(
                    IntelligenceCorrelation(
                        type=CorrelationType.SAME_COMPETITOR_MULTI_SOURCE,
                        title="Change confirmed across source types",
                        summary="Multiple independent source types contain recent evidence.",
                        competitor_ids=[competitor_id],
                        evidence_ids=[item.id for item in evidence[:5]],
                        confidence=min(0.95, 0.55 + len(source_types) * 0.1),
                    )
                )
            category_counts = Counter(item.category for item in evidence)
            for category, count in category_counts.items():
                if count >= 3:
                    correlation_type = {
                        IntelligenceCategory.FEATURES: CorrelationType.FEATURE_EXPANSION,
                        IntelligenceCategory.POSITIONING: CorrelationType.POSITIONING_SHIFT,
                    }.get(category, CorrelationType.REPEATED_CHANGE)
                    matches = [item for item in evidence if item.category == category]
                    summary = f"{count} recent evidence records share this category."
                    if category == IntelligenceCategory.PRICING:
                        directions = Counter(
                            direction
                            for item in matches
                            if (
                                direction := IntelligenceSearchService._pricing_direction(
                                    item.content
                                )
                            )
                        )
                        if directions and directions.most_common(1)[0][1] >= 2:
                            correlation_type = CorrelationType.PRICING_DIRECTION
                            direction, direction_count = directions.most_common(1)[0]
                            summary = (
                                f"{direction_count} observed pricing changes moved in the "
                                f"same {direction} direction."
                            )
                    correlations.append(
                        IntelligenceCorrelation(
                            type=correlation_type,
                            title=f"Repeated {category.value.replace('_', ' ')} activity",
                            summary=summary,
                            competitor_ids=[competitor_id],
                            evidence_ids=[item.id for item in matches[:5]],
                            confidence=min(0.9, 0.5 + count * 0.08),
                        )
                    )
        for (_, category), evidence in sorted(
            by_category.items(), key=lambda pair: (pair[0][0], pair[0][1].value)
        ):
            competitor_ids = sorted({item.competitor_id for item in evidence if item.competitor_id})
            if len(competitor_ids) >= 2:
                correlations.append(
                    IntelligenceCorrelation(
                        type=CorrelationType.MULTI_COMPETITOR_SAME_CATEGORY,
                        title=f"Cross-market {category.value.replace('_', ' ')} activity",
                        summary="Multiple competitors changed in the same evidence category.",
                        competitor_ids=competitor_ids,
                        evidence_ids=[item.id for item in evidence[:8]],
                        confidence=min(0.95, 0.55 + len(competitor_ids) * 0.08),
                    )
                )
        return correlations[:20]

    @staticmethod
    def _unavailable() -> AppError:
        return AppError(
            code="INTELLIGENCE_PROVIDER_UNAVAILABLE",
            message="Competitor intelligence search is temporarily unavailable.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
