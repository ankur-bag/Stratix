from collections.abc import Awaitable, Mapping
from typing import Any, cast

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.core.config import Settings
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


def _document(model: CompanyProfile | Source | InitializationJob | IdempotencyRecord) -> Document:
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

    async def update_progress(
        self,
        organization_id: str,
        job_id: str,
        stage: JobStage,
        progress_percent: int,
        message: str,
    ) -> InitializationJob | None:
        return await self._update(
            organization_id,
            job_id,
            {"stage": stage, "progress_percent": progress_percent, "message": message},
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
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
