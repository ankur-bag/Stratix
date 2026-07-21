from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.crawlers.models import (
    CrawlError,
    CrawlPageResult,
    CrawlRequest,
    CrawlResult,
)
from app.domain.intelligence import CompanyIntelligenceProfile, Snapshot
from app.domain.onboarding import (
    CompanyProfile,
    IdempotencyRecord,
    InitializationJob,
    JobStage,
    JobStatus,
    OnboardingStatus,
    Source,
    SourceStatus,
    utc_now,
)
from app.repositories.interfaces import RepositoryConflictError


class InMemoryCompanyProfileRepository:
    def __init__(self) -> None:
        self.items: dict[str, CompanyProfile] = {}

    async def create(self, profile: CompanyProfile) -> CompanyProfile:
        if any(item.organization_id == profile.organization_id for item in self.items.values()):
            raise RepositoryConflictError
        self.items[profile.id] = profile
        return profile

    async def get_for_organization(self, organization_id: str) -> CompanyProfile | None:
        return next(
            (item for item in self.items.values() if item.organization_id == organization_id),
            None,
        )

    async def get_by_id(
        self, organization_id: str, company_profile_id: str
    ) -> CompanyProfile | None:
        item = self.items.get(company_profile_id)
        return item if item and item.organization_id == organization_id else None

    async def update(
        self, organization_id: str, company_profile_id: str, changes: Mapping[str, Any]
    ) -> CompanyProfile | None:
        item = await self.get_by_id(organization_id, company_profile_id)
        if item is None:
            return None
        updated = item.model_copy(update={**changes, "updated_at": utc_now()})
        self.items[item.id] = updated
        return updated

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
        if await self.get_by_id(organization_id, company_profile_id):
            self.items.pop(company_profile_id)


class InMemorySourceRepository:
    def __init__(self) -> None:
        self.items: dict[str, Source] = {}

    async def create(self, source: Source) -> Source:
        if any(
            item.organization_id == source.organization_id
            and item.company_profile_id == source.company_profile_id
            and item.url == source.url
            for item in self.items.values()
        ):
            raise RepositoryConflictError
        self.items[source.id] = source
        return source

    async def get_primary_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> Source | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.organization_id == organization_id
                and item.company_profile_id == company_profile_id
            ),
            None,
        )

    async def list_for_company(self, organization_id: str, company_profile_id: str) -> list[Source]:
        return [
            item
            for item in self.items.values()
            if item.organization_id == organization_id
            and item.company_profile_id == company_profile_id
        ]

    async def mark_ready(self, organization_id: str, source_id: str) -> Source | None:
        item = self.items.get(source_id)
        if item is None or item.organization_id != organization_id:
            return None
        updated = item.model_copy(update={"status": SourceStatus.READY, "updated_at": utc_now()})
        self.items[source_id] = updated
        return updated

    async def update_url(self, organization_id: str, source_id: str, url: str) -> Source | None:
        item = self.items.get(source_id)
        if item is None or item.organization_id != organization_id:
            return None
        updated = item.model_copy(update={"url": url, "updated_at": utc_now()})
        self.items[source_id] = updated
        return updated

    async def mark_failed(self, organization_id: str, source_id: str) -> Source | None:
        item = self.items.get(source_id)
        if item is None or item.organization_id != organization_id:
            return None
        updated = item.model_copy(update={"status": SourceStatus.FAILED, "updated_at": utc_now()})
        self.items[source_id] = updated
        return updated

    async def delete(self, organization_id: str, source_id: str) -> None:
        item = self.items.get(source_id)
        if item and item.organization_id == organization_id:
            self.items.pop(source_id)


class InMemoryInitializationJobRepository:
    def __init__(self) -> None:
        self.items: dict[str, InitializationJob] = {}
        self.stage_history: list[JobStage] = []

    async def create(self, job: InitializationJob) -> InitializationJob:
        self.items[job.id] = job
        return job

    async def get_by_id(self, organization_id: str, job_id: str) -> InitializationJob | None:
        item = self.items.get(job_id)
        return item if item and item.organization_id == organization_id else None

    async def get_latest_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> InitializationJob | None:
        matches = [
            item
            for item in self.items.values()
            if item.organization_id == organization_id
            and item.company_profile_id == company_profile_id
        ]
        return max(matches, key=lambda item: item.created_at) if matches else None

    async def get_active_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> InitializationJob | None:
        matches = [
            item
            for item in self.items.values()
            if item.organization_id == organization_id
            and item.company_profile_id == company_profile_id
            and item.status in {JobStatus.QUEUED, JobStatus.RUNNING}
        ]
        return max(matches, key=lambda item: item.created_at) if matches else None

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
                "error_code": error_code,
                "error_message": error_message,
                "message": error_message,
                "completed_at": utc_now(),
            },
        )

    async def _update(
        self, organization_id: str, job_id: str, changes: Mapping[str, Any]
    ) -> InitializationJob | None:
        item = await self.get_by_id(organization_id, job_id)
        if item is None:
            return None
        updated = item.model_copy(update={**changes, "updated_at": utc_now()})
        self.items[job_id] = updated
        if "stage" in changes:
            self.stage_history.append(updated.stage)
        return updated

    async def delete(self, organization_id: str, job_id: str) -> None:
        if await self.get_by_id(organization_id, job_id):
            self.items.pop(job_id)


class InMemoryIdempotencyRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], IdempotencyRecord] = {}

    async def get(self, organization_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        return self.items.get((organization_id, idempotency_key))

    async def create(self, record: IdempotencyRecord) -> IdempotencyRecord:
        key = (record.organization_id, record.idempotency_key)
        if key in self.items:
            raise RepositoryConflictError
        self.items[key] = record
        return record

    async def save_response_reference(
        self,
        organization_id: str,
        idempotency_key: str,
        company_profile_id: str,
        initialization_job_id: str,
    ) -> IdempotencyRecord | None:
        record = await self.get(organization_id, idempotency_key)
        if record is None:
            return None
        updated = record.model_copy(
            update={
                "company_profile_id": company_profile_id,
                "initialization_job_id": initialization_job_id,
            }
        )
        self.items[(organization_id, idempotency_key)] = updated
        return updated

    async def delete(self, organization_id: str, idempotency_key: str) -> None:
        self.items.pop((organization_id, idempotency_key), None)


class InMemorySnapshotRepository:
    def __init__(self) -> None:
        self.items: dict[str, Snapshot] = {}

    async def create_many(self, snapshots: list[Snapshot]) -> list[Snapshot]:
        self.items.update({snapshot.id: snapshot for snapshot in snapshots})
        return snapshots

    async def list_for_company(
        self, organization_id: str, company_profile_id: str, *, limit: int = 100
    ) -> list[Snapshot]:
        matches = [
            item
            for item in self.items.values()
            if item.organization_id == organization_id
            and item.company_profile_id == company_profile_id
        ]
        return sorted(matches, key=lambda item: item.created_at, reverse=True)[:limit]


class InMemoryCompanyIntelligenceRepository:
    def __init__(self) -> None:
        self.items: dict[str, CompanyIntelligenceProfile] = {}

    async def save(self, profile: CompanyIntelligenceProfile) -> CompanyIntelligenceProfile:
        stored = profile
        for identifier, item in list(self.items.items()):
            if (
                item.organization_id == profile.organization_id
                and item.company_profile_id == profile.company_profile_id
            ):
                self.items.pop(identifier)
                stored = profile.model_copy(update={"id": item.id, "created_at": item.created_at})
        self.items[stored.id] = stored
        return stored

    async def get_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> CompanyIntelligenceProfile | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.organization_id == organization_id
                and item.company_profile_id == company_profile_id
            ),
            None,
        )


class InMemoryPersistence:
    def __init__(self, *, ready: bool = True) -> None:
        self.company_profiles = InMemoryCompanyProfileRepository()
        self.sources = InMemorySourceRepository()
        self.initialization_jobs = InMemoryInitializationJobRepository()
        self.idempotency_records = InMemoryIdempotencyRepository()
        self.snapshots = InMemorySnapshotRepository()
        self.company_intelligence_profiles = InMemoryCompanyIntelligenceRepository()
        self.ready = ready
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def ping(self) -> bool:
        return self.ready

    async def close(self) -> None:
        self.closed = True


class RecordingBackgroundExecutor:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, str]] = []

    def schedule(self, *, job_id: str, organization_id: str) -> None:
        self.scheduled.append((job_id, organization_id))

    async def close(self) -> None:
        return None


def crawl_page(
    *,
    url: str = "https://example.com",
    markdown: str = "# Example AI\n\n## Features\n\nReal-time voice agents",
    links: list[str] | None = None,
) -> CrawlPageResult:
    return CrawlPageResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        title="Example AI | Voice infrastructure",
        markdown=markdown,
        visible_text="Example AI Features Real-time voice agents",
        links=links or [],
        metadata={"description": "AI voice infrastructure for businesses"},
        fetched_at=datetime.now(UTC),
        crawler_used="fake",
    )


class FakeWebsiteCrawler:
    def __init__(
        self,
        *,
        pages: list[CrawlPageResult] | None = None,
        errors: list[CrawlError] | None = None,
    ) -> None:
        self.pages = pages if pages is not None else [crawl_page()]
        self.errors = errors or []
        self.requests: list[CrawlRequest] = []

    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return CrawlResult(
            pages=self.pages,
            errors=self.errors,
            started_at=now,
            completed_at=now,
        )
