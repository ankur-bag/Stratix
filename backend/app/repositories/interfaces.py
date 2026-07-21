from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

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


class Persistence(Protocol):
    company_profiles: CompanyProfileRepository
    sources: SourceRepository
    initialization_jobs: InitializationJobRepository
    idempotency_records: IdempotencyRepository
    snapshots: SnapshotRepository
    company_intelligence_profiles: CompanyIntelligenceRepository

    async def start(self) -> None: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


def mongo_update_fields(changes: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Build a safe mutable-field update document."""
    return {**changes, "updated_at": now}
