from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.crawlers.models import (
    CrawlError,
    CrawlPageResult,
    CrawlRequest,
    CrawlResult,
)
from app.domain.discovery import (
    CandidateStatus,
    Competitor,
    CompetitorCandidate,
    CompetitorSource,
    DiscoveryJob,
    DiscoveryJobStatus,
    DiscoveryStage,
    RelationshipType,
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


class InMemoryDiscoveryJobRepository:
    def __init__(self) -> None:
        self.items: dict[str, DiscoveryJob] = {}

    async def create(self, job: DiscoveryJob) -> DiscoveryJob:
        if any(
            item.organization_id == job.organization_id
            and item.idempotency_key == job.idempotency_key
            for item in self.items.values()
        ):
            raise RepositoryConflictError
        self.items[job.id] = job
        return job

    async def get_by_id(self, organization_id: str, job_id: str) -> DiscoveryJob | None:
        item = self.items.get(job_id)
        return item if item and item.organization_id == organization_id else None

    async def get_by_idempotency_key(
        self, organization_id: str, idempotency_key: str
    ) -> DiscoveryJob | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.organization_id == organization_id
                and item.idempotency_key == idempotency_key
            ),
            None,
        )

    async def get_latest_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> DiscoveryJob | None:
        matches = [
            item
            for item in self.items.values()
            if item.organization_id == organization_id
            and item.company_profile_id == company_profile_id
        ]
        return max(matches, key=lambda item: item.created_at) if matches else None

    async def get_active_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> DiscoveryJob | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.organization_id == organization_id
                and item.company_profile_id == company_profile_id
                and item.status in {DiscoveryJobStatus.QUEUED, DiscoveryJobStatus.RUNNING}
            ),
            None,
        )

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
                "message": "Discovery is complete. Review the candidates.",
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
        item = await self.get_by_id(organization_id, job_id)
        if item is None:
            return None
        updated = item.model_copy(update={**changes, "updated_at": utc_now()})
        self.items[job_id] = updated
        return updated


class InMemoryCompetitorCandidateRepository:
    def __init__(self) -> None:
        self.items: dict[str, CompetitorCandidate] = {}

    async def create_many(self, candidates: list[CompetitorCandidate]) -> list[CompetitorCandidate]:
        for candidate in candidates:
            if any(
                item.organization_id == candidate.organization_id
                and item.discovery_job_id == candidate.discovery_job_id
                and item.canonical_domain == candidate.canonical_domain
                for item in self.items.values()
            ):
                raise RepositoryConflictError
            self.items[candidate.id] = candidate
        return candidates

    async def get_by_id(
        self, organization_id: str, candidate_id: str
    ) -> CompetitorCandidate | None:
        item = self.items.get(candidate_id)
        return item if item and item.organization_id == organization_id else None

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
        items = sorted(
            (
                item
                for item in self.items.values()
                if item.organization_id == organization_id
                and (candidate_status is None or item.status == candidate_status)
                and (relationship_type is None or item.relationship_type == relationship_type)
                and item.similarity_score >= minimum_score
                and (discovery_job_id is None or item.discovery_job_id == discovery_job_id)
            ),
            key=lambda item: (item.rank, item.id),
        )
        if cursor:
            cursor_index = next(
                (index for index, item in enumerate(items) if item.id == cursor), None
            )
            if cursor_index is not None:
                items = items[cursor_index + 1 :]
        return items[:limit]

    async def mark_reviewed(
        self,
        organization_id: str,
        candidate_id: str,
        candidate_status: CandidateStatus,
        user_id: str,
    ) -> CompetitorCandidate | None:
        item = await self.get_by_id(organization_id, candidate_id)
        if item is None:
            return None
        updated = item.model_copy(
            update={
                "status": candidate_status,
                "reviewed_at": utc_now(),
                "reviewed_by_user_id": user_id,
                "updated_at": utc_now(),
            }
        )
        self.items[candidate_id] = updated
        return updated

    async def count_reviews(self, organization_id: str, discovery_job_id: str) -> tuple[int, int]:
        matching = [
            item
            for item in self.items.values()
            if item.organization_id == organization_id and item.discovery_job_id == discovery_job_id
        ]
        return (
            sum(item.status == CandidateStatus.APPROVED for item in matching),
            sum(item.status == CandidateStatus.REJECTED for item in matching),
        )


class InMemoryCompetitorRepository:
    def __init__(self) -> None:
        self.items: dict[str, Competitor] = {}

    async def create(self, competitor: Competitor) -> Competitor:
        if any(
            item.organization_id == competitor.organization_id
            and item.canonical_domain == competitor.canonical_domain
            for item in self.items.values()
        ):
            raise RepositoryConflictError
        self.items[competitor.id] = competitor
        return competitor

    async def get_by_candidate(self, organization_id: str, candidate_id: str) -> Competitor | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.organization_id == organization_id and item.candidate_id == candidate_id
            ),
            None,
        )

    async def get_by_domain(self, organization_id: str, canonical_domain: str) -> Competitor | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.organization_id == organization_id
                and item.canonical_domain == canonical_domain
            ),
            None,
        )

    async def list(self, organization_id: str, *, limit: int) -> list[Competitor]:
        return sorted(
            (item for item in self.items.values() if item.organization_id == organization_id),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]


class InMemoryCompetitorSourceRepository:
    def __init__(self) -> None:
        self.items: dict[str, CompetitorSource] = {}

    async def create(self, source: CompetitorSource) -> CompetitorSource:
        if any(
            item.organization_id == source.organization_id
            and item.competitor_id == source.competitor_id
            and item.url == source.url
            for item in self.items.values()
        ):
            raise RepositoryConflictError
        self.items[source.id] = source
        return source

    async def list_for_competitor(
        self, organization_id: str, competitor_id: str
    ) -> list[CompetitorSource]:
        return [
            item
            for item in self.items.values()
            if item.organization_id == organization_id and item.competitor_id == competitor_id
        ]


class InMemoryPersistence:
    def __init__(self, *, ready: bool = True) -> None:
        self.company_profiles = InMemoryCompanyProfileRepository()
        self.sources = InMemorySourceRepository()
        self.initialization_jobs = InMemoryInitializationJobRepository()
        self.idempotency_records = InMemoryIdempotencyRepository()
        self.snapshots = InMemorySnapshotRepository()
        self.company_intelligence_profiles = InMemoryCompanyIntelligenceRepository()
        self.discovery_jobs = InMemoryDiscoveryJobRepository()
        self.competitor_candidates = InMemoryCompetitorCandidateRepository()
        self.competitors = InMemoryCompetitorRepository()
        self.competitor_sources = InMemoryCompetitorSourceRepository()
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
