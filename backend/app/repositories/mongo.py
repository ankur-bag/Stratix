from collections.abc import Awaitable, Mapping
from datetime import datetime
from typing import Any, cast

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.core.config import Settings
from app.domain.discovery import (
    CandidateStatus,
    Competitor,
    CompetitorCandidate,
    DiscoveryJob,
    DiscoveryJobStatus,
    DiscoveryStage,
    RelationshipType,
)
from app.domain.intelligence import CompanyIntelligenceProfile, Snapshot
from app.domain.monitoring import CompetitorSource
from app.domain.monitoring import SourceStatus as CompetitorSourceStatus
from app.domain.onboarding import (
    CompanyProfile,
    IdempotencyRecord,
    InitializationJob,
    JobStage,
    JobStatus,
    OnboardingStatus,
    Source,
    SourceStatus,
    company_document,
    model_from_document,
    utc_now,
)
from app.domain.scanning import CompetitorScanJob, CompetitorSnapshot, ScanStage, ScanStatus
from app.domain.signals import CompetitorSignal, SignalStatus, SignalType
from app.repositories.interfaces import (
    RepositoryConflictError,
    RepositoryUnavailableError,
    mongo_update_fields,
)

Document = dict[str, Any]


async def _mongo[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except PyMongoError as exc:
        raise RepositoryUnavailableError from exc


def _document(
    model: CompanyProfile
    | Source
    | InitializationJob
    | IdempotencyRecord
    | Snapshot
    | CompanyIntelligenceProfile
    | DiscoveryJob
    | CompetitorCandidate
    | Competitor
    | CompetitorSource
    | CompetitorScanJob
    | CompetitorSnapshot
    | CompetitorSignal,
) -> Document:
    if isinstance(model, CompanyProfile):
        return company_document(model)
    document = model.model_dump(mode="python")
    document["_id"] = document.pop("id")
    return document


class MongoCompanyProfileRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, profile: CompanyProfile) -> CompanyProfile:
        try:
            await self._collection.insert_one(_document(profile))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return profile

    async def get_for_organization(self, organization_id: str) -> CompanyProfile | None:
        document = await _mongo(self._collection.find_one({"organization_id": organization_id}))
        return (
            cast(CompanyProfile, model_from_document(CompanyProfile, document))
            if document
            else None
        )

    async def get_by_id(
        self, organization_id: str, company_profile_id: str
    ) -> CompanyProfile | None:
        document = await _mongo(
            self._collection.find_one(
                {"_id": company_profile_id, "organization_id": organization_id}
            )
        )
        return (
            cast(CompanyProfile, model_from_document(CompanyProfile, document))
            if document
            else None
        )

    async def update(
        self, organization_id: str, company_profile_id: str, changes: Mapping[str, Any]
    ) -> CompanyProfile | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"_id": company_profile_id, "organization_id": organization_id},
                {"$set": mongo_update_fields(changes, utc_now())},
                return_document=ReturnDocument.AFTER,
            )
        )
        return (
            cast(CompanyProfile, model_from_document(CompanyProfile, document))
            if document
            else None
        )

    async def update_onboarding_status(
        self,
        organization_id: str,
        company_profile_id: str,
        status: OnboardingStatus,
        *,
        workspace_metadata: Mapping[str, str] | None = None,
    ) -> CompanyProfile | None:
        changes: dict[str, Any] = {"onboarding_status": status}
        if workspace_metadata is not None:
            changes["workspace_metadata"] = dict(workspace_metadata)
        return await self.update(organization_id, company_profile_id, changes)

    async def delete(self, organization_id: str, company_profile_id: str) -> None:
        await _mongo(
            self._collection.delete_one(
                {"_id": company_profile_id, "organization_id": organization_id}
            )
        )


class MongoSourceRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, source: Source) -> Source:
        try:
            await self._collection.insert_one(_document(source))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return source

    async def get_primary_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> Source | None:
        document = await _mongo(
            self._collection.find_one(
                {"organization_id": organization_id, "company_profile_id": company_profile_id}
            )
        )
        return cast(Source, model_from_document(Source, document)) if document else None

    async def list_for_company(self, organization_id: str, company_profile_id: str) -> list[Source]:
        cursor = self._collection.find(
            {"organization_id": organization_id, "company_profile_id": company_profile_id}
        )
        documents = await _mongo(cursor.to_list(length=100))
        return [cast(Source, model_from_document(Source, document)) for document in documents]

    async def mark_ready(self, organization_id: str, source_id: str) -> Source | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"_id": source_id, "organization_id": organization_id},
                {"$set": {"status": SourceStatus.READY, "updated_at": utc_now()}},
                return_document=ReturnDocument.AFTER,
            )
        )
        return cast(Source, model_from_document(Source, document)) if document else None

    async def update_url(self, organization_id: str, source_id: str, url: str) -> Source | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"_id": source_id, "organization_id": organization_id},
                {"$set": {"url": url, "updated_at": utc_now()}},
                return_document=ReturnDocument.AFTER,
            )
        )
        return cast(Source, model_from_document(Source, document)) if document else None

    async def mark_failed(self, organization_id: str, source_id: str) -> Source | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"_id": source_id, "organization_id": organization_id},
                {"$set": {"status": SourceStatus.FAILED, "updated_at": utc_now()}},
                return_document=ReturnDocument.AFTER,
            )
        )
        return cast(Source, model_from_document(Source, document)) if document else None

    async def delete(self, organization_id: str, source_id: str) -> None:
        await _mongo(
            self._collection.delete_one({"_id": source_id, "organization_id": organization_id})
        )


class MongoInitializationJobRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, job: InitializationJob) -> InitializationJob:
        try:
            await self._collection.insert_one(_document(job))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return job

    async def get_by_id(self, organization_id: str, job_id: str) -> InitializationJob | None:
        document = await _mongo(
            self._collection.find_one({"_id": job_id, "organization_id": organization_id})
        )
        return (
            cast(InitializationJob, model_from_document(InitializationJob, document))
            if document
            else None
        )

    async def get_latest_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> InitializationJob | None:
        document = await _mongo(
            self._collection.find_one(
                {"organization_id": organization_id, "company_profile_id": company_profile_id},
                sort=[("created_at", DESCENDING)],
            )
        )
        return (
            cast(InitializationJob, model_from_document(InitializationJob, document))
            if document
            else None
        )

    async def get_active_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> InitializationJob | None:
        document = await _mongo(
            self._collection.find_one(
                {
                    "organization_id": organization_id,
                    "company_profile_id": company_profile_id,
                    "status": {"$in": [JobStatus.QUEUED, JobStatus.RUNNING]},
                },
                sort=[("created_at", DESCENDING)],
            )
        )
        return (
            cast(InitializationJob, model_from_document(InitializationJob, document))
            if document
            else None
        )

    async def update_progress(
        self,
        organization_id: str,
        job_id: str,
        stage: JobStage,
        progress_percent: int,
        message: str,
        metrics: Mapping[str, int] | None = None,
    ) -> InitializationJob | None:
        changes: dict[str, Any] = {
            "stage": stage,
            "progress_percent": progress_percent,
            "message": message,
        }
        if metrics:
            changes.update(metrics)
        return await self._update(
            organization_id,
            job_id,
            changes,
        )

    async def mark_running(self, organization_id: str, job_id: str) -> InitializationJob | None:
        return await self._update(
            organization_id,
            job_id,
            {"status": JobStatus.RUNNING, "started_at": utc_now()},
        )

    async def mark_succeeded(self, organization_id: str, job_id: str) -> InitializationJob | None:
        return await self._update(
            organization_id,
            job_id,
            {
                "status": JobStatus.SUCCEEDED,
                "stage": JobStage.COMPLETED,
                "progress_percent": 100,
                "message": "Company workspace initialization is complete.",
                "completed_at": utc_now(),
            },
        )

    async def mark_failed(
        self,
        organization_id: str,
        job_id: str,
        error_code: str,
        error_message: str,
    ) -> InitializationJob | None:
        return await self._update(
            organization_id,
            job_id,
            {
                "status": JobStatus.FAILED,
                "stage": JobStage.FAILED,
                "message": error_message,
                "error_code": error_code,
                "error_message": error_message,
                "completed_at": utc_now(),
            },
        )

    async def _update(
        self, organization_id: str, job_id: str, changes: Mapping[str, Any]
    ) -> InitializationJob | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"_id": job_id, "organization_id": organization_id},
                {"$set": mongo_update_fields(changes, utc_now())},
                return_document=ReturnDocument.AFTER,
            )
        )
        return (
            cast(InitializationJob, model_from_document(InitializationJob, document))
            if document
            else None
        )

    async def delete(self, organization_id: str, job_id: str) -> None:
        await _mongo(
            self._collection.delete_one({"_id": job_id, "organization_id": organization_id})
        )


class MongoIdempotencyRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def get(self, organization_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        document = await _mongo(
            self._collection.find_one(
                {"organization_id": organization_id, "idempotency_key": idempotency_key}
            )
        )
        return (
            cast(IdempotencyRecord, model_from_document(IdempotencyRecord, document))
            if document
            else None
        )

    async def create(self, record: IdempotencyRecord) -> IdempotencyRecord:
        try:
            await self._collection.insert_one(_document(record))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return record

    async def save_response_reference(
        self,
        organization_id: str,
        idempotency_key: str,
        company_profile_id: str,
        initialization_job_id: str,
    ) -> IdempotencyRecord | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"organization_id": organization_id, "idempotency_key": idempotency_key},
                {
                    "$set": {
                        "company_profile_id": company_profile_id,
                        "initialization_job_id": initialization_job_id,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        )
        return (
            cast(IdempotencyRecord, model_from_document(IdempotencyRecord, document))
            if document
            else None
        )

    async def delete(self, organization_id: str, idempotency_key: str) -> None:
        await _mongo(
            self._collection.delete_one(
                {"organization_id": organization_id, "idempotency_key": idempotency_key}
            )
        )


class MongoSnapshotRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create_many(self, snapshots: list[Snapshot]) -> list[Snapshot]:
        if not snapshots:
            return []
        try:
            await self._collection.insert_many([_document(snapshot) for snapshot in snapshots])
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return snapshots

    async def list_for_company(
        self, organization_id: str, company_profile_id: str, *, limit: int = 100
    ) -> list[Snapshot]:
        cursor = self._collection.find(
            {"organization_id": organization_id, "company_profile_id": company_profile_id}
        ).sort("created_at", DESCENDING)
        documents = await _mongo(cursor.to_list(length=limit))
        return [cast(Snapshot, model_from_document(Snapshot, item)) for item in documents]


class MongoCompanyIntelligenceRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def save(self, profile: CompanyIntelligenceProfile) -> CompanyIntelligenceProfile:
        query = {
            "organization_id": profile.organization_id,
            "company_profile_id": profile.company_profile_id,
        }
        try:
            existing = await self._collection.find_one(query)
            stored = (
                profile.model_copy(
                    update={
                        "id": str(existing["_id"]),
                        "created_at": existing["created_at"],
                    }
                )
                if existing
                else profile
            )
            await self._collection.replace_one(query, _document(stored), upsert=True)
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return stored

    async def get_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> CompanyIntelligenceProfile | None:
        document = await _mongo(
            self._collection.find_one(
                {"organization_id": organization_id, "company_profile_id": company_profile_id}
            )
        )
        return (
            cast(
                CompanyIntelligenceProfile,
                model_from_document(CompanyIntelligenceProfile, document),
            )
            if document
            else None
        )


class MongoDiscoveryJobRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, job: DiscoveryJob) -> DiscoveryJob:
        try:
            await self._collection.insert_one(_document(job))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return job

    async def get_by_id(self, organization_id: str, job_id: str) -> DiscoveryJob | None:
        document = await _mongo(
            self._collection.find_one({"_id": job_id, "organization_id": organization_id})
        )
        return cast(DiscoveryJob, model_from_document(DiscoveryJob, document)) if document else None

    async def get_by_idempotency_key(
        self, organization_id: str, idempotency_key: str
    ) -> DiscoveryJob | None:
        document = await _mongo(
            self._collection.find_one(
                {"organization_id": organization_id, "idempotency_key": idempotency_key}
            )
        )
        return cast(DiscoveryJob, model_from_document(DiscoveryJob, document)) if document else None

    async def get_latest_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> DiscoveryJob | None:
        document = await _mongo(
            self._collection.find_one(
                {
                    "organization_id": organization_id,
                    "company_profile_id": company_profile_id,
                },
                sort=[("created_at", DESCENDING)],
            )
        )
        return cast(DiscoveryJob, model_from_document(DiscoveryJob, document)) if document else None

    async def get_active_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> DiscoveryJob | None:
        document = await _mongo(
            self._collection.find_one(
                {
                    "organization_id": organization_id,
                    "company_profile_id": company_profile_id,
                    "status": {"$in": [DiscoveryJobStatus.QUEUED, DiscoveryJobStatus.RUNNING]},
                },
                sort=[("created_at", DESCENDING)],
            )
        )
        return cast(DiscoveryJob, model_from_document(DiscoveryJob, document)) if document else None

    async def mark_running(self, organization_id: str, job_id: str) -> DiscoveryJob | None:
        return await self._update(
            organization_id,
            job_id,
            {"status": DiscoveryJobStatus.RUNNING, "started_at": utc_now()},
        )

    async def set_queries(
        self, organization_id: str, job_id: str, queries: list[str]
    ) -> DiscoveryJob | None:
        return await self._update(
            organization_id, job_id, {"queries": queries, "query_count": len(queries)}
        )

    async def update_progress(
        self,
        organization_id: str,
        job_id: str,
        stage: DiscoveryStage,
        progress_percent: int,
        message: str,
    ) -> DiscoveryJob | None:
        return await self._update(
            organization_id,
            job_id,
            {"stage": stage, "progress_percent": progress_percent, "message": message},
        )

    async def mark_awaiting_review(
        self,
        organization_id: str,
        job_id: str,
        *,
        candidate_count: int,
        provider_error_count: int,
        partial: bool,
    ) -> DiscoveryJob | None:
        return await self._update(
            organization_id,
            job_id,
            {
                "status": (
                    DiscoveryJobStatus.PARTIALLY_SUCCEEDED
                    if partial
                    else DiscoveryJobStatus.SUCCEEDED
                ),
                "stage": DiscoveryStage.AWAITING_USER_REVIEW,
                "progress_percent": 100,
                "message": (
                    "Discovery completed with partial search coverage. Review the candidates."
                    if partial
                    else "Discovery is complete. Review the candidates."
                ),
                "candidate_count": candidate_count,
                "provider_error_count": provider_error_count,
                "completed_at": utc_now(),
            },
        )

    async def mark_failed(
        self, organization_id: str, job_id: str, error_code: str, error_message: str
    ) -> DiscoveryJob | None:
        return await self._update(
            organization_id,
            job_id,
            {
                "status": DiscoveryJobStatus.FAILED,
                "stage": DiscoveryStage.FAILED,
                "message": error_message,
                "error_code": error_code,
                "error_message": error_message,
                "completed_at": utc_now(),
            },
        )

    async def update_review_counts(
        self, organization_id: str, job_id: str, accepted: int, rejected: int
    ) -> DiscoveryJob | None:
        return await self._update(
            organization_id,
            job_id,
            {"accepted_count": accepted, "rejected_count": rejected},
        )

    async def _update(
        self, organization_id: str, job_id: str, changes: Mapping[str, Any]
    ) -> DiscoveryJob | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"_id": job_id, "organization_id": organization_id},
                {"$set": mongo_update_fields(changes, utc_now())},
                return_document=ReturnDocument.AFTER,
            )
        )
        return cast(DiscoveryJob, model_from_document(DiscoveryJob, document)) if document else None


class MongoCompetitorCandidateRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create_many(self, candidates: list[CompetitorCandidate]) -> list[CompetitorCandidate]:
        if not candidates:
            return []
        try:
            await self._collection.insert_many([_document(item) for item in candidates])
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return candidates

    async def get_by_id(
        self, organization_id: str, candidate_id: str
    ) -> CompetitorCandidate | None:
        document = await _mongo(
            self._collection.find_one({"_id": candidate_id, "organization_id": organization_id})
        )
        return (
            cast(CompetitorCandidate, model_from_document(CompetitorCandidate, document))
            if document
            else None
        )

    async def list(
        self,
        organization_id: str,
        *,
        candidate_status: CandidateStatus | None,
        relationship_type: RelationshipType | None,
        minimum_score: float,
        discovery_job_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[CompetitorCandidate]:
        query: dict[str, Any] = {
            "organization_id": organization_id,
            "similarity_score": {"$gte": minimum_score},
        }
        if candidate_status is not None:
            query["status"] = candidate_status
        if relationship_type is not None:
            query["relationship_type"] = relationship_type
        if discovery_job_id is not None:
            query["discovery_job_id"] = discovery_job_id
        if cursor is not None:
            cursor_document = await _mongo(
                self._collection.find_one({"_id": cursor, "organization_id": organization_id})
            )
            if cursor_document:
                query["$or"] = [
                    {"rank": {"$gt": cursor_document["rank"]}},
                    {"rank": cursor_document["rank"], "_id": {"$gt": cursor}},
                ]
        documents = await _mongo(
            self._collection.find(query)
            .sort([("rank", ASCENDING), ("_id", ASCENDING)])
            .to_list(length=limit)
        )
        return [
            cast(CompetitorCandidate, model_from_document(CompetitorCandidate, item))
            for item in documents
        ]

    async def mark_reviewed(
        self,
        organization_id: str,
        candidate_id: str,
        candidate_status: CandidateStatus,
        user_id: str,
    ) -> CompetitorCandidate | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"_id": candidate_id, "organization_id": organization_id},
                {
                    "$set": {
                        "status": candidate_status,
                        "reviewed_at": utc_now(),
                        "reviewed_by_user_id": user_id,
                        "updated_at": utc_now(),
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        )
        return (
            cast(CompetitorCandidate, model_from_document(CompetitorCandidate, document))
            if document
            else None
        )

    async def count_reviews(self, organization_id: str, discovery_job_id: str) -> tuple[int, int]:
        accepted = await _mongo(
            self._collection.count_documents(
                {
                    "organization_id": organization_id,
                    "discovery_job_id": discovery_job_id,
                    "status": CandidateStatus.APPROVED,
                }
            )
        )
        rejected = await _mongo(
            self._collection.count_documents(
                {
                    "organization_id": organization_id,
                    "discovery_job_id": discovery_job_id,
                    "status": CandidateStatus.REJECTED,
                }
            )
        )
        return accepted, rejected


class MongoCompetitorRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, competitor: Competitor) -> Competitor:
        try:
            await self._collection.insert_one(_document(competitor))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return competitor

    async def get_by_candidate(self, organization_id: str, candidate_id: str) -> Competitor | None:
        document = await _mongo(
            self._collection.find_one(
                {"organization_id": organization_id, "candidate_id": candidate_id}
            )
        )
        return cast(Competitor, model_from_document(Competitor, document)) if document else None

    async def get_by_domain(self, organization_id: str, canonical_domain: str) -> Competitor | None:
        document = await _mongo(
            self._collection.find_one(
                {"organization_id": organization_id, "canonical_domain": canonical_domain}
            )
        )
        return cast(Competitor, model_from_document(Competitor, document)) if document else None

    async def get_by_id(self, organization_id: str, competitor_id: str) -> Competitor | None:
        document = await _mongo(
            self._collection.find_one({"organization_id": organization_id, "_id": competitor_id})
        )
        return cast(Competitor, model_from_document(Competitor, document)) if document else None

    async def update(
        self, organization_id: str, competitor_id: str, changes: Mapping[str, Any]
    ) -> Competitor | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"organization_id": organization_id, "_id": competitor_id},
                {"$set": mongo_update_fields(changes, utc_now())},
                return_document=ReturnDocument.AFTER,
            )
        )
        return cast(Competitor, model_from_document(Competitor, document)) if document else None

    async def list(self, organization_id: str, *, limit: int) -> list[Competitor]:
        documents = await _mongo(
            self._collection.find({"organization_id": organization_id})
            .sort("created_at", DESCENDING)
            .to_list(length=limit)
        )
        return [cast(Competitor, model_from_document(Competitor, item)) for item in documents]


class MongoCompetitorSourceRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, source: CompetitorSource) -> CompetitorSource:
        try:
            await self._collection.insert_one(_document(source))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return source

    async def list_for_competitor(
        self,
        organization_id: str,
        competitor_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        enabled_only: bool = False,
    ) -> list[CompetitorSource]:
        query: Document = {"organization_id": organization_id, "competitor_id": competitor_id}
        if cursor:
            query["_id"] = {"$gt": cursor}
        if enabled_only:
            query["enabled"] = True
        documents = await _mongo(
            self._collection.find(query).sort("_id", ASCENDING).to_list(length=limit)
        )
        return [
            cast(CompetitorSource, model_from_document(CompetitorSource, item))
            for item in documents
        ]

    async def get_by_id(
        self, organization_id: str, competitor_id: str, source_id: str
    ) -> CompetitorSource | None:
        document = await _mongo(
            self._collection.find_one(
                {
                    "organization_id": organization_id,
                    "competitor_id": competitor_id,
                    "_id": source_id,
                }
            )
        )
        return (
            cast(CompetitorSource, model_from_document(CompetitorSource, document))
            if document
            else None
        )

    async def update(
        self,
        organization_id: str,
        competitor_id: str,
        source_id: str,
        changes: Mapping[str, Any],
    ) -> CompetitorSource | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {
                    "organization_id": organization_id,
                    "competitor_id": competitor_id,
                    "_id": source_id,
                },
                {"$set": mongo_update_fields(changes, utc_now())},
                return_document=ReturnDocument.AFTER,
            )
        )
        return (
            cast(CompetitorSource, model_from_document(CompetitorSource, document))
            if document
            else None
        )

    async def set_status(
        self,
        organization_id: str,
        competitor_id: str,
        source_id: str,
        source_status: CompetitorSourceStatus,
        *,
        error_code: str | None = None,
    ) -> CompetitorSource | None:
        return await self.update(
            organization_id,
            competitor_id,
            source_id,
            {"status": source_status, "last_error_code": error_code},
        )


class MongoCompetitorScanRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, scan: CompetitorScanJob) -> CompetitorScanJob:
        try:
            await self._collection.insert_one(_document(scan))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return scan

    async def get_by_id(self, organization_id: str, scan_id: str) -> CompetitorScanJob | None:
        document = await _mongo(
            self._collection.find_one({"organization_id": organization_id, "_id": scan_id})
        )
        return (
            cast(CompetitorScanJob, model_from_document(CompetitorScanJob, document))
            if document
            else None
        )

    async def get_by_idempotency_key(
        self, organization_id: str, idempotency_key: str
    ) -> CompetitorScanJob | None:
        document = await _mongo(
            self._collection.find_one(
                {"organization_id": organization_id, "idempotency_key": idempotency_key}
            )
        )
        return (
            cast(CompetitorScanJob, model_from_document(CompetitorScanJob, document))
            if document
            else None
        )

    async def get_active_for_competitor(
        self, organization_id: str, competitor_id: str
    ) -> CompetitorScanJob | None:
        document = await _mongo(
            self._collection.find_one(
                {
                    "organization_id": organization_id,
                    "competitor_id": competitor_id,
                    "status": {"$in": [ScanStatus.QUEUED, ScanStatus.RUNNING]},
                },
                sort=[("created_at", DESCENDING)],
            )
        )
        return (
            cast(CompetitorScanJob, model_from_document(CompetitorScanJob, document))
            if document
            else None
        )

    async def list_for_competitor(
        self,
        organization_id: str,
        competitor_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> list[CompetitorScanJob]:
        query: Document = {
            "organization_id": organization_id,
            "competitor_id": competitor_id,
        }
        if cursor:
            cursor_document = await _mongo(
                self._collection.find_one({"organization_id": organization_id, "_id": cursor})
            )
            if cursor_document:
                query["$or"] = [
                    {"created_at": {"$lt": cursor_document["created_at"]}},
                    {
                        "created_at": cursor_document["created_at"],
                        "_id": {"$lt": cursor},
                    },
                ]
        documents = await _mongo(
            self._collection.find(query)
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .to_list(length=limit)
        )
        return [
            cast(CompetitorScanJob, model_from_document(CompetitorScanJob, item))
            for item in documents
        ]

    async def update_progress(
        self,
        organization_id: str,
        scan_id: str,
        stage: ScanStage,
        progress_percent: int,
        message: str,
        metrics: Mapping[str, int] | None = None,
    ) -> CompetitorScanJob | None:
        changes: Document = {
            "stage": stage,
            "progress_percent": progress_percent,
            "message": message,
            **dict(metrics or {}),
        }
        return await self._update(organization_id, scan_id, changes)

    async def mark_running(self, organization_id: str, scan_id: str) -> CompetitorScanJob | None:
        return await self._update(
            organization_id,
            scan_id,
            {"status": ScanStatus.RUNNING, "started_at": utc_now()},
        )

    async def finalize(
        self,
        organization_id: str,
        scan_id: str,
        scan_status: ScanStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> CompetitorScanJob | None:
        return await self._update(
            organization_id,
            scan_id,
            {
                "status": scan_status,
                "stage": ScanStage.FAILED
                if scan_status == ScanStatus.FAILED
                else ScanStage.COMPLETED,
                "progress_percent": 100,
                "message": "The competitor scan could not be completed."
                if scan_status == ScanStatus.FAILED
                else "Competitor scan completed.",
                "error_code": error_code,
                "error_message": error_message,
                "completed_at": utc_now(),
            },
        )

    async def _update(
        self, organization_id: str, scan_id: str, changes: Mapping[str, Any]
    ) -> CompetitorScanJob | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"organization_id": organization_id, "_id": scan_id},
                {"$set": mongo_update_fields(changes, utc_now())},
                return_document=ReturnDocument.AFTER,
            )
        )
        return (
            cast(CompetitorScanJob, model_from_document(CompetitorScanJob, document))
            if document
            else None
        )


class MongoCompetitorSnapshotRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, snapshot: CompetitorSnapshot) -> CompetitorSnapshot:
        try:
            await self._collection.insert_one(_document(snapshot))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return snapshot

    async def get_by_id(self, organization_id: str, snapshot_id: str) -> CompetitorSnapshot | None:
        document = await _mongo(
            self._collection.find_one({"organization_id": organization_id, "_id": snapshot_id})
        )
        return (
            cast(CompetitorSnapshot, model_from_document(CompetitorSnapshot, document))
            if document
            else None
        )

    async def get_latest_for_source(
        self, organization_id: str, source_id: str
    ) -> CompetitorSnapshot | None:
        document = await _mongo(
            self._collection.find_one(
                {"organization_id": organization_id, "source_id": source_id},
                sort=[("fetched_at", DESCENDING), ("created_at", DESCENDING)],
            )
        )
        return (
            cast(CompetitorSnapshot, model_from_document(CompetitorSnapshot, document))
            if document
            else None
        )

    async def get_for_scan_source(
        self, organization_id: str, scan_id: str, source_id: str
    ) -> CompetitorSnapshot | None:
        document = await _mongo(
            self._collection.find_one(
                {
                    "organization_id": organization_id,
                    "scan_id": scan_id,
                    "source_id": source_id,
                }
            )
        )
        return (
            cast(CompetitorSnapshot, model_from_document(CompetitorSnapshot, document))
            if document
            else None
        )


class MongoCompetitorSignalRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, signal: CompetitorSignal) -> CompetitorSignal:
        try:
            await self._collection.insert_one(_document(signal))
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return signal

    async def get_by_id(self, organization_id: str, signal_id: str) -> CompetitorSignal | None:
        document = await _mongo(
            self._collection.find_one({"organization_id": organization_id, "_id": signal_id})
        )
        return (
            cast(CompetitorSignal, model_from_document(CompetitorSignal, document))
            if document
            else None
        )

    async def list_for_competitor(
        self,
        organization_id: str,
        competitor_id: str,
        *,
        signal_type: SignalType | None,
        signal_status: SignalStatus | None,
        minimum_importance: float,
        source_id: str | None,
        observed_after: datetime | None,
        observed_before: datetime | None,
        scan_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[CompetitorSignal]:
        query: Document = {
            "organization_id": organization_id,
            "competitor_id": competitor_id,
            "importance_score": {"$gte": minimum_importance},
        }
        if signal_type is not None:
            query["type"] = signal_type
        if signal_status is not None:
            query["status"] = signal_status
        if source_id is not None:
            query["source_id"] = source_id
        if scan_id is not None:
            query["scan_id"] = scan_id
        if observed_after is not None or observed_before is not None:
            query["observed_at"] = {
                **({"$gte": observed_after} if observed_after is not None else {}),
                **({"$lte": observed_before} if observed_before is not None else {}),
            }
        if cursor:
            cursor_document = await _mongo(
                self._collection.find_one({"organization_id": organization_id, "_id": cursor})
            )
            if cursor_document:
                query["$or"] = [
                    {"observed_at": {"$lt": cursor_document["observed_at"]}},
                    {
                        "observed_at": cursor_document["observed_at"],
                        "_id": {"$lt": cursor},
                    },
                ]
        documents = await _mongo(
            self._collection.find(query)
            .sort([("observed_at", DESCENDING), ("_id", DESCENDING)])
            .to_list(length=limit)
        )
        return [
            cast(CompetitorSignal, model_from_document(CompetitorSignal, item))
            for item in documents
        ]

    async def update_status(
        self, organization_id: str, signal_id: str, signal_status: SignalStatus
    ) -> CompetitorSignal | None:
        document = await _mongo(
            self._collection.find_one_and_update(
                {"organization_id": organization_id, "_id": signal_id},
                {"$set": {"status": signal_status}},
                return_document=ReturnDocument.AFTER,
            )
        )
        return (
            cast(CompetitorSignal, model_from_document(CompetitorSignal, document))
            if document
            else None
        )


class MongoPersistence:
    """Own the PyMongo client lifecycle, repositories, and collection indexes."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncMongoClient[Document] | None = None
        self._database: AsyncDatabase[Document] | None = None
        self.company_profiles: MongoCompanyProfileRepository
        self.sources: MongoSourceRepository
        self.initialization_jobs: MongoInitializationJobRepository
        self.idempotency_records: MongoIdempotencyRepository
        self.snapshots: MongoSnapshotRepository
        self.company_intelligence_profiles: MongoCompanyIntelligenceRepository
        self.discovery_jobs: MongoDiscoveryJobRepository
        self.competitor_candidates: MongoCompetitorCandidateRepository
        self.competitors: MongoCompetitorRepository
        self.competitor_sources: MongoCompetitorSourceRepository
        self.competitor_scans: MongoCompetitorScanRepository
        self.competitor_snapshots: MongoCompetitorSnapshotRepository
        self.competitor_signals: MongoCompetitorSignalRepository

    async def start(self) -> None:
        self._client = AsyncMongoClient(
            self._settings.mongodb_uri,
            connectTimeoutMS=self._settings.mongodb_connect_timeout_ms,
            serverSelectionTimeoutMS=self._settings.mongodb_server_selection_timeout_ms,
            tz_aware=True,
        )
        self._database = self._client[self._settings.mongodb_database]
        self.company_profiles = MongoCompanyProfileRepository(self._database["company_profiles"])
        self.sources = MongoSourceRepository(self._database["sources"])
        self.initialization_jobs = MongoInitializationJobRepository(
            self._database["initialization_jobs"]
        )
        self.idempotency_records = MongoIdempotencyRepository(self._database["idempotency_records"])
        self.snapshots = MongoSnapshotRepository(self._database["snapshots"])
        self.company_intelligence_profiles = MongoCompanyIntelligenceRepository(
            self._database["company_intelligence_profiles"]
        )
        self.discovery_jobs = MongoDiscoveryJobRepository(
            self._database["competitor_discovery_jobs"]
        )
        self.competitor_candidates = MongoCompetitorCandidateRepository(
            self._database["competitor_candidates"]
        )
        self.competitors = MongoCompetitorRepository(self._database["competitors"])
        self.competitor_sources = MongoCompetitorSourceRepository(
            self._database["competitor_sources"]
        )
        self.competitor_scans = MongoCompetitorScanRepository(
            self._database["competitor_scan_jobs"]
        )
        self.competitor_snapshots = MongoCompetitorSnapshotRepository(
            self._database["competitor_snapshots"]
        )
        self.competitor_signals = MongoCompetitorSignalRepository(self._database["signals"])
        if await self.ping():
            await self._create_indexes()

    async def ping(self) -> bool:
        if self._database is None:
            return False
        try:
            await self._database.command("ping")
        except PyMongoError:
            return False
        return True

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def _create_indexes(self) -> None:
        if self._database is None:
            raise RepositoryUnavailableError
        try:
            company = self._database["company_profiles"]
            await company.create_index("organization_id", unique=True)
            await company.create_index(
                [("organization_id", ASCENDING), ("onboarding_status", ASCENDING)]
            )
            await company.create_index([("organization_id", ASCENDING), ("created_at", ASCENDING)])
            sources = self._database["sources"]
            await sources.create_index(
                [("organization_id", ASCENDING), ("company_profile_id", ASCENDING)]
            )
            await sources.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("company_profile_id", ASCENDING),
                    ("url", ASCENDING),
                ],
                unique=True,
            )
            await sources.create_index([("organization_id", ASCENDING), ("enabled", ASCENDING)])
            jobs = self._database["initialization_jobs"]
            await jobs.create_index([("organization_id", ASCENDING), ("created_at", ASCENDING)])
            await jobs.create_index([("organization_id", ASCENDING), ("status", ASCENDING)])
            await jobs.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("company_profile_id", ASCENDING),
                    ("created_at", ASCENDING),
                ]
            )
            records = self._database["idempotency_records"]
            await records.create_index(
                [("organization_id", ASCENDING), ("idempotency_key", ASCENDING)], unique=True
            )
            await records.create_index("expires_at", expireAfterSeconds=0)
            snapshots = self._database["snapshots"]
            await snapshots.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("company_profile_id", ASCENDING),
                    ("created_at", ASCENDING),
                ]
            )
            await snapshots.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("source_id", ASCENDING),
                    ("fetched_at", ASCENDING),
                ]
            )
            await snapshots.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("source_id", ASCENDING),
                    ("final_url", ASCENDING),
                    ("fetched_at", ASCENDING),
                ]
            )
            await snapshots.create_index(
                [("organization_id", ASCENDING), ("content_hash", ASCENDING)]
            )
            intelligence = self._database["company_intelligence_profiles"]
            await intelligence.create_index(
                [("organization_id", ASCENDING), ("company_profile_id", ASCENDING)],
                unique=True,
            )
            discovery_jobs = self._database["competitor_discovery_jobs"]
            await discovery_jobs.create_index(
                [("organization_id", ASCENDING), ("created_at", ASCENDING)]
            )
            await discovery_jobs.create_index(
                [("organization_id", ASCENDING), ("status", ASCENDING)]
            )
            await discovery_jobs.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("company_profile_id", ASCENDING),
                    ("status", ASCENDING),
                ]
            )
            await discovery_jobs.create_index(
                [("organization_id", ASCENDING), ("idempotency_key", ASCENDING)],
                unique=True,
            )
            candidates = self._database["competitor_candidates"]
            await candidates.create_index(
                [("organization_id", ASCENDING), ("discovery_job_id", ASCENDING)]
            )
            await candidates.create_index(
                [("organization_id", ASCENDING), ("status", ASCENDING), ("rank", ASCENDING)]
            )
            await candidates.create_index(
                [("organization_id", ASCENDING), ("canonical_domain", ASCENDING)]
            )
            await candidates.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("discovery_job_id", ASCENDING),
                    ("canonical_domain", ASCENDING),
                ],
                unique=True,
            )
            competitors = self._database["competitors"]
            await competitors.create_index(
                [("organization_id", ASCENDING), ("created_at", ASCENDING)]
            )
            await competitors.create_index([("organization_id", ASCENDING), ("status", ASCENDING)])
            await competitors.create_index(
                [("organization_id", ASCENDING), ("canonical_domain", ASCENDING)],
                unique=True,
            )
            await competitors.create_index(
                [("organization_id", ASCENDING), ("candidate_id", ASCENDING)], unique=True
            )
            competitor_sources = self._database["competitor_sources"]
            await competitor_sources.create_index(
                [("organization_id", ASCENDING), ("competitor_id", ASCENDING)]
            )
            await competitor_sources.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("competitor_id", ASCENDING),
                    ("canonical_url", ASCENDING),
                ],
                unique=True,
            )
            await competitor_sources.create_index(
                [("organization_id", ASCENDING), ("status", ASCENDING)]
            )
            competitor_scans = self._database["competitor_scan_jobs"]
            await competitor_scans.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("competitor_id", ASCENDING),
                    ("created_at", DESCENDING),
                ]
            )
            await competitor_scans.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("competitor_id", ASCENDING),
                    ("status", ASCENDING),
                ]
            )
            await competitor_scans.create_index(
                [("organization_id", ASCENDING), ("idempotency_key", ASCENDING)],
                unique=True,
            )
            competitor_snapshots = self._database["competitor_snapshots"]
            await competitor_snapshots.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("competitor_id", ASCENDING),
                    ("created_at", DESCENDING),
                ]
            )
            await competitor_snapshots.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("source_id", ASCENDING),
                    ("fetched_at", DESCENDING),
                ]
            )
            await competitor_snapshots.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("source_id", ASCENDING),
                    ("content_hash", ASCENDING),
                ]
            )
            await competitor_snapshots.create_index(
                [("organization_id", ASCENDING), ("scan_id", ASCENDING)]
            )
            await competitor_snapshots.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("scan_id", ASCENDING),
                    ("source_id", ASCENDING),
                ],
                unique=True,
            )
            await competitor_snapshots.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("competitor_id", ASCENDING),
                    ("page_type", ASCENDING),
                    ("fetched_at", DESCENDING),
                ]
            )
            signals = self._database["signals"]
            await signals.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("competitor_id", ASCENDING),
                    ("observed_at", DESCENDING),
                ]
            )
            await signals.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("competitor_id", ASCENDING),
                    ("status", ASCENDING),
                ]
            )
            await signals.create_index(
                [
                    ("organization_id", ASCENDING),
                    ("competitor_id", ASCENDING),
                    ("type", ASCENDING),
                ]
            )
            await signals.create_index([("organization_id", ASCENDING), ("scan_id", ASCENDING)])
            await signals.create_index(
                [("organization_id", ASCENDING), ("current_snapshot_id", ASCENDING)]
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
