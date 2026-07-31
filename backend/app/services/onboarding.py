import hashlib
import json
import re
from datetime import timedelta
from typing import Any, Protocol

from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.domain.intelligence import (
    CompanyIntelligenceProfile,
    ReinitializeResponse,
    Snapshot,
)
from app.domain.onboarding import (
    CompanyOnboardingRequest,
    CompanyProfile,
    CompanySummary,
    CompanyUpdateRequest,
    IdempotencyRecord,
    InitializationJob,
    JobStage,
    JobStatus,
    JobSummary,
    OnboardingResponse,
    OnboardingStatus,
    OnboardingStatusResponse,
    Source,
    SourceStatus,
    SourceType,
    new_id,
    utc_now,
)
from app.repositories.interfaces import (
    Persistence,
    RepositoryConflictError,
    RepositoryUnavailableError,
)
from app.services.initialization import BackgroundExecutor

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class OnboardingDocumentService(Protocol):
    async def attach_for_onboarding(
        self,
        *,
        context: AuthenticatedContext,
        company_profile_id: str,
        document_ids: list[str],
    ) -> Any: ...


class CompanyOnboardingService:
    """Coordinate organization-scoped company onboarding and retrieval."""

    def __init__(
        self,
        persistence: Persistence,
        background_executor: BackgroundExecutor,
        document_service: OnboardingDocumentService | None = None,
        rebuild_executor: BackgroundExecutor | None = None,
    ) -> None:
        self._persistence = persistence
        self._background_executor = background_executor
        self._document_service = document_service
        self._rebuild_executor = rebuild_executor or background_executor

    async def submit(
        self,
        *,
        context: AuthenticatedContext,
        idempotency_key: str,
        request: CompanyOnboardingRequest,
    ) -> OnboardingResponse:
        key = idempotency_key.strip()
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
            raise AppError(
                code="INVALID_IDEMPOTENCY_KEY",
                message="A valid Idempotency-Key header is required.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        fingerprint = self._fingerprint(request)
        try:
            existing_record = await self._persistence.idempotency_records.get(
                context.organization_id, key
            )
            if existing_record is not None:
                if existing_record.request_fingerprint != fingerprint:
                    raise AppError(
                        code="IDEMPOTENCY_CONFLICT",
                        message="The idempotency key was already used for another request.",
                        status_code=status.HTTP_409_CONFLICT,
                    )
                return await self._response_from_record(context, existing_record)
            existing_company = await self._persistence.company_profiles.get_for_organization(
                context.organization_id
            )
            if existing_company is not None:
                raise self._company_exists_error()
            return await self._create_submission(context, key, fingerprint, request)
        except RepositoryUnavailableError as exc:
            raise self._database_unavailable_error() from exc

    async def get_company(self, context: AuthenticatedContext) -> CompanyProfile:
        try:
            company = await self._persistence.company_profiles.get_for_organization(
                context.organization_id
            )
        except RepositoryUnavailableError as exc:
            raise self._database_unavailable_error() from exc
        if company is None:
            raise AppError(
                code="COMPANY_PROFILE_NOT_FOUND",
                message="No company profile exists for this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return company

    async def update_company(
        self, context: AuthenticatedContext, request: CompanyUpdateRequest
    ) -> CompanyProfile:
        company = await self.get_company(context)
        changes = request.model_dump(exclude_unset=True, mode="python")
        if not changes:
            return company
        try:
            if "website_url" in changes:
                source = await self._persistence.sources.get_primary_for_company(
                    context.organization_id, company.id
                )
                if source is None:
                    raise AppError(
                        code="INITIALIZATION_FAILED",
                        message="The primary company website source is unavailable.",
                        status_code=status.HTTP_409_CONFLICT,
                    )
                await self._persistence.sources.update_url(
                    context.organization_id, source.id, changes["website_url"]
                )
            updated = await self._persistence.company_profiles.update(
                context.organization_id, company.id, changes
            )
        except RepositoryUnavailableError as exc:
            raise self._database_unavailable_error() from exc
        if updated is None:
            raise AppError(
                code="COMPANY_PROFILE_NOT_FOUND",
                message="No company profile exists for this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return updated

    async def get_onboarding_status(
        self, context: AuthenticatedContext
    ) -> OnboardingStatusResponse:
        try:
            company = await self._persistence.company_profiles.get_for_organization(
                context.organization_id
            )
            if company is None:
                return OnboardingStatusResponse(
                    has_company_profile=False,
                    onboarding_status=None,
                    company_profile_id=None,
                    latest_job=None,
                )
            job = await self._persistence.initialization_jobs.get_latest_for_company(
                context.organization_id, company.id
            )
        except RepositoryUnavailableError as exc:
            raise self._database_unavailable_error() from exc
        return OnboardingStatusResponse(
            has_company_profile=True,
            onboarding_status=company.onboarding_status,
            company_profile_id=company.id,
            latest_job=self._job_summary(job) if job else None,
        )

    async def get_job(self, context: AuthenticatedContext, job_id: str) -> InitializationJob:
        try:
            job = await self._persistence.initialization_jobs.get_by_id(
                context.organization_id, job_id
            )
        except RepositoryUnavailableError as exc:
            raise self._database_unavailable_error() from exc
        if job is None:
            raise AppError(
                code="INITIALIZATION_JOB_NOT_FOUND",
                message="The requested initialization job was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return job

    async def get_snapshots(self, context: AuthenticatedContext) -> list[Snapshot]:
        company = await self.get_company(context)
        try:
            return await self._persistence.snapshots.list_for_company(
                context.organization_id, company.id
            )
        except RepositoryUnavailableError as exc:
            raise self._database_unavailable_error() from exc

    async def get_intelligence_profile(
        self, context: AuthenticatedContext
    ) -> CompanyIntelligenceProfile:
        company = await self.get_company(context)
        try:
            profile = await self._persistence.company_intelligence_profiles.get_for_company(
                context.organization_id, company.id
            )
        except RepositoryUnavailableError as exc:
            raise self._database_unavailable_error() from exc
        if profile is None:
            raise AppError(
                code="COMPANY_INTELLIGENCE_PROFILE_NOT_FOUND",
                message="The company intelligence profile is not available yet.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return profile

    async def reinitialize(
        self, *, context: AuthenticatedContext, idempotency_key: str
    ) -> ReinitializeResponse:
        key = idempotency_key.strip()
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
            raise AppError(
                code="INVALID_IDEMPOTENCY_KEY",
                message="A valid Idempotency-Key header is required.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        company = await self.get_company(context)
        fingerprint = hashlib.sha256(f"reinitialize:{company.id}".encode()).hexdigest()
        try:
            existing = await self._persistence.idempotency_records.get(context.organization_id, key)
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise AppError(
                        code="IDEMPOTENCY_CONFLICT",
                        message="The idempotency key was already used for another request.",
                        status_code=status.HTTP_409_CONFLICT,
                    )
                job = await self._persistence.initialization_jobs.get_by_id(
                    context.organization_id, existing.initialization_job_id
                )
                if job is None:
                    raise AppError(
                        code="IDEMPOTENCY_CONFLICT",
                        message="The stored idempotency result is incomplete.",
                        status_code=status.HTTP_409_CONFLICT,
                    )
                return self._reinitialize_response(job)
            active = await self._persistence.initialization_jobs.get_active_for_company(
                context.organization_id, company.id
            )
            if active is not None:
                raise AppError(
                    code="INITIALIZATION_ALREADY_RUNNING",
                    message="Company initialization is already running.",
                    status_code=status.HTTP_409_CONFLICT,
                )
            now = utc_now()
            job = InitializationJob(
                id=new_id(),
                organization_id=context.organization_id,
                company_profile_id=company.id,
                requested_by_user_id=context.user_id,
                status=JobStatus.QUEUED,
                stage=JobStage.QUEUED,
                progress_percent=0,
                message="Company workspace reinitialization is queued.",
                created_at=now,
                updated_at=now,
            )
            record = IdempotencyRecord(
                id=new_id(),
                organization_id=context.organization_id,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                company_profile_id=company.id,
                initialization_job_id=job.id,
                created_at=now,
                expires_at=now + timedelta(days=7),
            )
            await self._persistence.initialization_jobs.create(job)
            try:
                await self._persistence.idempotency_records.create(record)
            except Exception:
                await self._persistence.initialization_jobs.delete(context.organization_id, job.id)
                raise
        except RepositoryConflictError as exc:
            raise AppError(
                code="IDEMPOTENCY_CONFLICT",
                message="The idempotency key is already in use.",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        except RepositoryUnavailableError as exc:
            raise self._database_unavailable_error() from exc
        await self._rebuild_executor.schedule(
            job_id=job.id, organization_id=context.organization_id
        )
        return self._reinitialize_response(job)

    async def _create_submission(
        self,
        context: AuthenticatedContext,
        key: str,
        fingerprint: str,
        request: CompanyOnboardingRequest,
    ) -> OnboardingResponse:
        now = utc_now()
        company = CompanyProfile(
            **request.model_dump(mode="python", exclude={"document_ids"}),
            id=new_id(),
            organization_id=context.organization_id,
            created_by_user_id=context.user_id,
            onboarding_status=OnboardingStatus.SUBMITTED,
            created_at=now,
            updated_at=now,
            submitted_at=now,
        )
        source = Source(
            id=new_id(),
            organization_id=context.organization_id,
            company_profile_id=company.id,
            type=SourceType.WEBSITE,
            url=company.website_url,
            enabled=True,
            status=SourceStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        job = InitializationJob(
            id=new_id(),
            organization_id=context.organization_id,
            company_profile_id=company.id,
            requested_by_user_id=context.user_id,
            status=JobStatus.QUEUED,
            stage=JobStage.QUEUED,
            progress_percent=0,
            message="Company workspace initialization is queued.",
            created_at=now,
            updated_at=now,
        )
        record = IdempotencyRecord(
            id=new_id(),
            organization_id=context.organization_id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            company_profile_id=company.id,
            initialization_job_id=job.id,
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
        created_source = False
        created_job = False
        try:
            await self._persistence.company_profiles.create(company)
            await self._persistence.sources.create(source)
            created_source = True
            await self._persistence.initialization_jobs.create(job)
            created_job = True
            await self._persistence.idempotency_records.create(record)
            if request.document_ids:
                if self._document_service is None:
                    raise AppError(
                        code="DOCUMENT_INGESTION_UNAVAILABLE",
                        message="Document ingestion is temporarily unavailable.",
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )
                await self._document_service.attach_for_onboarding(
                    context=context,
                    company_profile_id=company.id,
                    document_ids=request.document_ids,
                )
        except RepositoryConflictError as exc:
            await self._compensate(context.organization_id, company.id, source.id, job.id, key)
            raise self._company_exists_error() from exc
        except Exception:
            if created_source or created_job:
                await self._compensate(context.organization_id, company.id, source.id, job.id, key)
            else:
                await self._persistence.company_profiles.delete(context.organization_id, company.id)
            raise
        await self._background_executor.schedule(
            job_id=job.id, organization_id=context.organization_id
        )
        return self._response(company, job)

    async def _compensate(
        self,
        organization_id: str,
        company_id: str,
        source_id: str,
        job_id: str,
        key: str,
    ) -> None:
        await self._persistence.idempotency_records.delete(organization_id, key)
        await self._persistence.initialization_jobs.delete(organization_id, job_id)
        await self._persistence.sources.delete(organization_id, source_id)
        await self._persistence.company_profiles.delete(organization_id, company_id)

    async def _response_from_record(
        self, context: AuthenticatedContext, record: IdempotencyRecord
    ) -> OnboardingResponse:
        company = await self._persistence.company_profiles.get_by_id(
            context.organization_id, record.company_profile_id
        )
        job = await self._persistence.initialization_jobs.get_by_id(
            context.organization_id, record.initialization_job_id
        )
        if company is None or job is None:
            raise AppError(
                code="IDEMPOTENCY_CONFLICT",
                message="The stored idempotency result is incomplete.",
                status_code=status.HTTP_409_CONFLICT,
            )
        return self._response(company, job)

    @staticmethod
    def _response(company: CompanyProfile, job: InitializationJob) -> OnboardingResponse:
        return OnboardingResponse(
            company_profile=CompanySummary.model_validate(company, from_attributes=True),
            initialization_job=CompanyOnboardingService._job_summary(job),
        )

    @staticmethod
    def _job_summary(job: InitializationJob) -> JobSummary:
        return JobSummary(
            id=job.id,
            status=job.status,
            stage=job.stage,
            progress_percent=job.progress_percent,
            message=job.message,
            crawl_pages_discovered=job.crawl_pages_discovered,
            crawl_pages_processed=job.crawl_pages_processed,
            snapshot_count=job.snapshot_count,
        )

    @staticmethod
    def _reinitialize_response(job: InitializationJob) -> ReinitializeResponse:
        return ReinitializeResponse(
            initialization_job_id=job.id,
            status=job.status,
            stage=job.stage,
            progress_percent=job.progress_percent,
        )

    @staticmethod
    def _fingerprint(request: CompanyOnboardingRequest) -> str:
        payload = json.dumps(
            request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _company_exists_error() -> AppError:
        return AppError(
            code="COMPANY_PROFILE_ALREADY_EXISTS",
            message="This organization already has a company profile.",
            status_code=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _database_unavailable_error() -> AppError:
        return AppError(
            code="DATABASE_UNAVAILABLE",
            message="The database is temporarily unavailable.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
