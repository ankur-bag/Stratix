from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from app.domain.discovery import (
    CandidateStatus,
    Competitor,
    CompetitorCandidate,
    CompetitorSource,
    DiscoveryJob,
    DiscoveryStage,
    RelationshipType,
)
from app.domain.intelligence import CompanyIntelligenceProfile, Snapshot
from app.domain.onboarding import (
    CompanyProfile,
    IdempotencyRecord,
    InitializationJob,
    JobStage,
    OnboardingStatus,
    Source,
)


class RepositoryConflictError(Exception):
    """A unique persistence invariant was violated."""


class RepositoryUnavailableError(Exception):
    """The persistence provider could not complete an operation."""


class CompanyProfileRepository(Protocol):
    async def create(self, profile: CompanyProfile) -> CompanyProfile: ...
    async def get_for_organization(self, organization_id: str) -> CompanyProfile | None: ...
    async def get_by_id(
        self, organization_id: str, company_profile_id: str
    ) -> CompanyProfile | None: ...
    async def update(
        self, organization_id: str, company_profile_id: str, changes: Mapping[str, Any]
    ) -> CompanyProfile | None: ...
    async def update_onboarding_status(
        self,
        organization_id: str,
        company_profile_id: str,
        status: OnboardingStatus,
        *,
        workspace_metadata: Mapping[str, str] | None = None,
    ) -> CompanyProfile | None: ...
    async def delete(self, organization_id: str, company_profile_id: str) -> None: ...


class SourceRepository(Protocol):
    async def create(self, source: Source) -> Source: ...
    async def get_primary_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> Source | None: ...
    async def list_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> list[Source]: ...
    async def mark_ready(self, organization_id: str, source_id: str) -> Source | None: ...
    async def update_url(self, organization_id: str, source_id: str, url: str) -> Source | None: ...
    async def mark_failed(self, organization_id: str, source_id: str) -> Source | None: ...
    async def delete(self, organization_id: str, source_id: str) -> None: ...


class InitializationJobRepository(Protocol):
    async def create(self, job: InitializationJob) -> InitializationJob: ...
    async def get_by_id(self, organization_id: str, job_id: str) -> InitializationJob | None: ...
    async def get_latest_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> InitializationJob | None: ...
    async def get_active_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> InitializationJob | None: ...
    async def update_progress(
        self,
        organization_id: str,
        job_id: str,
        stage: JobStage,
        progress_percent: int,
        message: str,
        metrics: Mapping[str, int] | None = None,
    ) -> InitializationJob | None: ...
    async def mark_running(self, organization_id: str, job_id: str) -> InitializationJob | None: ...
    async def mark_succeeded(
        self, organization_id: str, job_id: str
    ) -> InitializationJob | None: ...
    async def mark_failed(
        self,
        organization_id: str,
        job_id: str,
        error_code: str,
        error_message: str,
    ) -> InitializationJob | None: ...
    async def delete(self, organization_id: str, job_id: str) -> None: ...


class IdempotencyRepository(Protocol):
    async def get(self, organization_id: str, idempotency_key: str) -> IdempotencyRecord | None: ...
    async def create(self, record: IdempotencyRecord) -> IdempotencyRecord: ...
    async def save_response_reference(
        self,
        organization_id: str,
        idempotency_key: str,
        company_profile_id: str,
        initialization_job_id: str,
    ) -> IdempotencyRecord | None: ...
    async def delete(self, organization_id: str, idempotency_key: str) -> None: ...


class SnapshotRepository(Protocol):
    async def create_many(self, snapshots: list[Snapshot]) -> list[Snapshot]: ...
    async def list_for_company(
        self, organization_id: str, company_profile_id: str, *, limit: int = 100
    ) -> list[Snapshot]: ...


class CompanyIntelligenceRepository(Protocol):
    async def save(self, profile: CompanyIntelligenceProfile) -> CompanyIntelligenceProfile: ...
    async def get_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> CompanyIntelligenceProfile | None: ...


class DiscoveryJobRepository(Protocol):
    async def create(self, job: DiscoveryJob) -> DiscoveryJob: ...
    async def get_by_id(self, organization_id: str, job_id: str) -> DiscoveryJob | None: ...
    async def get_by_idempotency_key(
        self, organization_id: str, idempotency_key: str
    ) -> DiscoveryJob | None: ...
    async def get_latest_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> DiscoveryJob | None: ...
    async def get_active_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> DiscoveryJob | None: ...
    async def mark_running(self, organization_id: str, job_id: str) -> DiscoveryJob | None: ...
    async def set_queries(
        self, organization_id: str, job_id: str, queries: list[str]
    ) -> DiscoveryJob | None: ...
    async def update_progress(
        self,
        organization_id: str,
        job_id: str,
        stage: DiscoveryStage,
        progress_percent: int,
        message: str,
    ) -> DiscoveryJob | None: ...
    async def mark_awaiting_review(
        self,
        organization_id: str,
        job_id: str,
        *,
        candidate_count: int,
        provider_error_count: int,
        partial: bool,
    ) -> DiscoveryJob | None: ...
    async def mark_failed(
        self, organization_id: str, job_id: str, error_code: str, error_message: str
    ) -> DiscoveryJob | None: ...
    async def update_review_counts(
        self, organization_id: str, job_id: str, accepted: int, rejected: int
    ) -> DiscoveryJob | None: ...


class CompetitorCandidateRepository(Protocol):
    async def create_many(
        self, candidates: list[CompetitorCandidate]
    ) -> list[CompetitorCandidate]: ...
    async def get_by_id(
        self, organization_id: str, candidate_id: str
    ) -> CompetitorCandidate | None: ...
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
    ) -> list[CompetitorCandidate]: ...
    async def mark_reviewed(
        self,
        organization_id: str,
        candidate_id: str,
        candidate_status: CandidateStatus,
        user_id: str,
    ) -> CompetitorCandidate | None: ...
    async def count_reviews(
        self, organization_id: str, discovery_job_id: str
    ) -> tuple[int, int]: ...


class CompetitorRepository(Protocol):
    async def create(self, competitor: Competitor) -> Competitor: ...
    async def get_by_candidate(
        self, organization_id: str, candidate_id: str
    ) -> Competitor | None: ...
    async def get_by_domain(
        self, organization_id: str, canonical_domain: str
    ) -> Competitor | None: ...
    async def list(self, organization_id: str, *, limit: int) -> list[Competitor]: ...


class CompetitorSourceRepository(Protocol):
    async def create(self, source: CompetitorSource) -> CompetitorSource: ...
    async def list_for_competitor(
        self, organization_id: str, competitor_id: str
    ) -> list[CompetitorSource]: ...


class Persistence(Protocol):
    company_profiles: CompanyProfileRepository
    sources: SourceRepository
    initialization_jobs: InitializationJobRepository
    idempotency_records: IdempotencyRepository
    snapshots: SnapshotRepository
    company_intelligence_profiles: CompanyIntelligenceRepository
    discovery_jobs: DiscoveryJobRepository
    competitor_candidates: CompetitorCandidateRepository
    competitors: CompetitorRepository
    competitor_sources: CompetitorSourceRepository

    async def start(self) -> None: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


def mongo_update_fields(changes: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Build a safe mutable-field update document."""
    return {**changes, "updated_at": now}
