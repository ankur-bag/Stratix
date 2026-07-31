import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Protocol

from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.background_jobs import (
    BackgroundJob,
    BackgroundJobPage,
    BackgroundJobStatus,
    JobExecutionResult,
    JobType,
    NewBackgroundJob,
    NotificationSeverity,
    NotificationType,
    RetryJobResponse,
)
from app.domain.documents import DocumentJobStatus, DocumentStage, DocumentStatus
from app.domain.onboarding import JobStatus
from app.domain.scanning import ScanStatus
from app.domain.strategy import StrategyRunStatus
from app.repositories.interfaces import (
    Persistence,
    RepositoryConflictError,
    RepositoryUnavailableError,
)

logger = get_logger(__name__)


class JobExecutionFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def classify_job_error(error: BaseException) -> tuple[str, str, bool]:
    if isinstance(error, JobExecutionFailure):
        return error.code, error.message, error.retryable
    if isinstance(error, (TimeoutError, ConnectionError, RepositoryUnavailableError)):
        return (
            "TEMPORARY_PROVIDER_FAILURE",
            "A temporary dependency failure interrupted the job.",
            True,
        )
    code = getattr(error, "code", None)
    non_retryable = {
        "INVALID_CRAWL_TARGET",
        "CRAWL_PRIVATE_NETWORK_BLOCKED",
        "DOCUMENT_INVALID_PDF",
        "DOCUMENT_PASSWORD_PROTECTED",
        "DOCUMENT_PAGE_LIMIT_EXCEEDED",
        "DOCUMENT_NOT_FOUND",
        "PINECONE_DIMENSION_MISMATCH",
        "INVALID_EMBEDDING_CONFIGURATION",
        "JOB_PAYLOAD_VERSION_UNSUPPORTED",
        "JOB_PAYLOAD_INVALID",
    }
    if isinstance(code, str):
        return (
            code,
            str(getattr(error, "message", "The job could not be completed.")),
            code not in non_retryable,
        )
    return (
        "BACKGROUND_JOB_FAILED",
        "The background job could not be completed.",
        False,
    )


def retry_delay_seconds(
    *,
    attempt_count: int,
    base_seconds: float,
    maximum_seconds: float,
    jitter_ratio: float,
    jitter_source: Callable[[], float] = random.random,
) -> float:
    bounded = min(maximum_seconds, base_seconds * (2 ** max(0, attempt_count - 1)))
    centered = max(-1.0, min(1.0, jitter_source() * 2 - 1))
    return float(max(0.0, bounded + bounded * jitter_ratio * centered))


async def mark_domain_cancelled(persistence: Persistence, job: BackgroundJob) -> None:
    message = "The background job was cancelled."
    if job.job_type in {
        JobType.COMPANY_INITIALIZATION,
        JobType.COMPANY_INTELLIGENCE_REBUILD,
    }:
        await persistence.initialization_jobs.mark_failed(
            job.organization_id, job.domain_resource_id, "JOB_CANCELLED", message
        )
    elif job.job_type in {
        JobType.COMPETITOR_BASELINE_SCAN,
        JobType.COMPETITOR_RESCAN,
    }:
        await persistence.competitor_scans.finalize(
            job.organization_id,
            job.domain_resource_id,
            ScanStatus.FAILED,
            error_code="JOB_CANCELLED",
            error_message=message,
        )
    elif job.job_type == JobType.DOCUMENT_INGESTION:
        ingestion = await persistence.document_ingestion_jobs.finalize(
            job.organization_id,
            job.domain_resource_id,
            DocumentJobStatus.FAILED,
            error_code="JOB_CANCELLED",
            error_message=message,
        )
        if ingestion:
            document = await persistence.company_documents.get_by_id(
                job.organization_id, ingestion.document_id
            )
            if document:
                await persistence.company_documents.update(
                    job.organization_id,
                    document.id,
                    {
                        "status": (
                            DocumentStatus.READY
                            if document.active_version is not None
                            else DocumentStatus.FAILED
                        ),
                        "pending_version": None,
                        "error_code": "JOB_CANCELLED",
                        "error_message": message,
                    },
                )
    elif job.job_type == JobType.STRATEGIC_ANALYSIS:
        await persistence.strategy_runs.update(
            job.organization_id,
            job.domain_resource_id,
            {
                "status": StrategyRunStatus.CANCELLED,
                "stage": "cancelled",
                "message": message,
                "error_code": "STRATEGIC_AGENT_RUN_CANCELLED",
                "error_message": message,
                "cancelled_at": datetime.now(UTC),
                "completed_at": datetime.now(UTC),
            },
        )


class BackgroundJobQueue:
    def __init__(
        self,
        persistence: Persistence,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self._persistence = persistence
        self._settings = settings
        self._clock = clock or SystemClock()

    async def enqueue(
        self,
        *,
        organization_id: str,
        company_profile_id: str | None,
        job_type: JobType,
        domain_resource_type: str,
        domain_resource_id: str,
        deduplication_key: str,
        payload: dict[str, str | int | bool | None] | None = None,
        created_by_user_id: str | None = None,
        scheduled_for: datetime | None = None,
        priority: int = 0,
        enforce_backpressure: bool = True,
        deduplicate_terminal: bool = False,
    ) -> BackgroundJob:
        existing = (
            await self._persistence.background_jobs.get_by_deduplication_key(
                organization_id, deduplication_key
            )
            if deduplicate_terminal
            else await self._persistence.background_jobs.get_active_by_deduplication_key(
                organization_id, deduplication_key
            )
        )
        if existing is not None:
            return existing
        if enforce_backpressure:
            await self._check_backpressure(organization_id, job_type)
        now = self._clock.now()
        draft = NewBackgroundJob(
            organization_id=organization_id,
            company_profile_id=company_profile_id,
            job_type=job_type,
            domain_resource_type=domain_resource_type,
            domain_resource_id=domain_resource_id,
            priority=priority,
            deduplication_key=deduplication_key,
            payload=payload or {},
            max_attempts=self._settings.job_max_attempts_default,
            available_at=scheduled_for or now,
            scheduled_for=scheduled_for,
            created_by_user_id=created_by_user_id,
        )
        try:
            return await self._persistence.background_jobs.enqueue(job=draft)
        except RepositoryConflictError:
            existing = (
                await self._persistence.background_jobs.get_by_deduplication_key(
                    organization_id, deduplication_key
                )
                if deduplicate_terminal
                else await self._persistence.background_jobs.get_active_by_deduplication_key(
                    organization_id, deduplication_key
                )
            )
            if existing is not None:
                return existing
            raise

    async def _check_backpressure(self, organization_id: str, job_type: JobType) -> None:
        if (
            await self._persistence.background_jobs.count_active_for_organization(organization_id)
            >= self._settings.max_active_jobs_per_organization
        ):
            raise JobExecutionFailure(
                "ORGANIZATION_JOB_LIMIT_REACHED",
                "This workspace has too many active background jobs.",
                retryable=True,
            )
        if (
            await self._persistence.background_jobs.count_queued()
            >= self._settings.max_global_queued_jobs
        ):
            raise JobExecutionFailure(
                "GLOBAL_JOB_LIMIT_REACHED",
                "The background queue is temporarily full.",
                retryable=True,
            )
        if job_type in {
            JobType.COMPETITOR_BASELINE_SCAN,
            JobType.COMPETITOR_RESCAN,
        } and (
            await self._persistence.background_jobs.count_queued(
                organization_id=organization_id, scan_only=True
            )
            >= self._settings.max_queued_scans_per_organization
        ):
            raise JobExecutionFailure(
                "ORGANIZATION_SCAN_LIMIT_REACHED",
                "This workspace has too many queued competitor scans.",
                retryable=True,
            )


class DurableJobExecutor:
    """Adapt an existing domain executor boundary to the durable queue."""

    def __init__(
        self,
        queue: BackgroundJobQueue,
        persistence: Persistence,
        job_type: JobType,
        domain_resource_type: str,
    ) -> None:
        self._queue = queue
        self._persistence = persistence
        self._job_type = job_type
        self._domain_resource_type = domain_resource_type

    async def schedule(self, *, job_id: str, organization_id: str) -> None:
        company_profile_id: str | None = None
        user_id: str | None = None
        if self._job_type in {
            JobType.COMPANY_INITIALIZATION,
            JobType.COMPANY_INTELLIGENCE_REBUILD,
        }:
            initialization = await self._persistence.initialization_jobs.get_by_id(
                organization_id, job_id
            )
            if initialization:
                company_profile_id = initialization.company_profile_id
                user_id = initialization.requested_by_user_id
        elif self._job_type == JobType.DOCUMENT_INGESTION:
            ingestion = await self._persistence.document_ingestion_jobs.get_by_id(
                organization_id, job_id
            )
            if ingestion:
                company_profile_id = ingestion.company_profile_id
                user_id = ingestion.requested_by_user_id
        await self._queue.enqueue(
            organization_id=organization_id,
            company_profile_id=company_profile_id,
            job_type=self._job_type,
            domain_resource_type=self._domain_resource_type,
            domain_resource_id=job_id,
            deduplication_key=f"{self._job_type.value}:{job_id}",
            created_by_user_id=user_id,
        )

    async def close(self) -> None:
        return None


class DurableCompetitorScanExecutor:
    def __init__(self, queue: BackgroundJobQueue, persistence: Persistence) -> None:
        self._queue = queue
        self._persistence = persistence

    async def schedule(self, *, scan_id: str, organization_id: str) -> BackgroundJob | None:
        scan = await self._persistence.competitor_scans.get_by_id(organization_id, scan_id)
        if scan is None:
            raise JobExecutionFailure(
                "COMPETITOR_SCAN_NOT_FOUND",
                "The competitor scan was not found.",
                retryable=False,
            )
        job_type = (
            JobType.COMPETITOR_BASELINE_SCAN
            if scan.scan_type.value == "baseline"
            else JobType.COMPETITOR_RESCAN
        )
        company = await self._persistence.company_profiles.get_for_organization(organization_id)
        return await self._queue.enqueue(
            organization_id=organization_id,
            company_profile_id=company.id if company else None,
            job_type=job_type,
            domain_resource_type="competitor_scan",
            domain_resource_id=scan.id,
            deduplication_key=f"competitor-scan:{scan.competitor_id}",
            payload={"competitor_id": scan.competitor_id},
            created_by_user_id=scan.requested_by_user_id,
        )

    async def close(self) -> None:
        return None


class JobRunner(Protocol):
    async def run(self, *, job_id: str, organization_id: str) -> None: ...


class ScanRunner(Protocol):
    async def run(self, *, scan_id: str, organization_id: str) -> None: ...


class ScanEvidenceScheduler(Protocol):
    async def schedule_scan(
        self,
        *,
        organization_id: str,
        company_profile_id: str | None,
        scan_id: str,
        competitor_id: str,
        created_by_user_id: str | None,
    ) -> dict[str, int]: ...


class BackgroundJobHandler(Protocol):
    job_type: JobType

    async def execute(
        self, *, job: BackgroundJob, context: "JobExecutionContext"
    ) -> JobExecutionResult: ...


@dataclass
class JobExecutionContext:
    persistence: Persistence
    clock: Clock
    worker_id: str
    lease_lost: asyncio.Event
    shutdown_requested: asyncio.Event
    _last_progress_at: float = field(default=0.0)
    _last_progress_percent: int = field(default=-1)
    progress_min_interval_seconds: float = 2
    progress_min_percent_delta: int = 2

    async def cancelled(self, job: BackgroundJob) -> bool:
        current = await self.persistence.background_jobs.get_by_id(job.organization_id, job.id)
        return (
            self.lease_lost.is_set()
            or current is None
            or current.status == BackgroundJobStatus.CANCEL_REQUESTED
        )

    async def report_progress(
        self, job: BackgroundJob, percent: int, stage: str, message: str
    ) -> bool:
        elapsed = monotonic() - self._last_progress_at
        delta = abs(percent - self._last_progress_percent)
        if (
            self._last_progress_percent >= 0
            and elapsed < self.progress_min_interval_seconds
            and delta < self.progress_min_percent_delta
        ):
            return True
        updated = await self.persistence.background_jobs.update_progress(
            job_id=job.id,
            worker_id=self.worker_id,
            now=self.clock.now(),
            progress_percent=percent,
            stage=stage,
            message=message,
        )
        if updated is None:
            self.lease_lost.set()
            return False
        self._last_progress_at = monotonic()
        self._last_progress_percent = percent
        return True


class CompanyInitializationJobHandler:
    job_type = JobType.COMPANY_INITIALIZATION

    def __init__(self, runner: JobRunner, persistence: Persistence) -> None:
        self._runner = runner
        self._persistence = persistence

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        await context.report_progress(job, 2, "initializing", "Preparing company intelligence.")
        await self._runner.run(job_id=job.domain_resource_id, organization_id=job.organization_id)
        domain = await self._persistence.initialization_jobs.get_by_id(
            job.organization_id, job.domain_resource_id
        )
        if domain is None:
            raise JobExecutionFailure(
                "INITIALIZATION_JOB_NOT_FOUND",
                "The initialization record no longer exists.",
                retryable=False,
            )
        if domain.status == JobStatus.FAILED:
            raise JobExecutionFailure(
                domain.error_code or "INITIALIZATION_FAILED",
                domain.error_message or "Company initialization failed.",
                retryable=(domain.error_code or "") not in {"INVALID_CRAWL_TARGET"},
            )
        return JobExecutionResult(
            message="Company intelligence initialization completed.",
            result_summary={"domain_status": domain.status.value},
        )


class CompanyIntelligenceRebuildJobHandler(CompanyInitializationJobHandler):
    job_type = JobType.COMPANY_INTELLIGENCE_REBUILD


class CompetitorScanJobHandler:
    def __init__(
        self,
        job_type: JobType,
        runner: ScanRunner,
        persistence: Persistence,
        evidence_scheduler: ScanEvidenceScheduler | None = None,
    ) -> None:
        self.job_type = job_type
        self._runner = runner
        self._persistence = persistence
        self._evidence_scheduler = evidence_scheduler

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        competitor_id = str(job.payload.get("competitor_id", ""))
        competitor = await self._persistence.competitors.get_by_id(
            job.organization_id, competitor_id
        )
        if competitor is None:
            raise JobExecutionFailure(
                "COMPETITOR_NOT_FOUND",
                "The competitor no longer exists in this workspace.",
                retryable=False,
            )
        await context.report_progress(job, 2, "scanning", "Preparing competitor scan.")
        await self._runner.run(scan_id=job.domain_resource_id, organization_id=job.organization_id)
        scan = await self._persistence.competitor_scans.get_by_id(
            job.organization_id, job.domain_resource_id
        )
        if scan is None:
            raise JobExecutionFailure(
                "COMPETITOR_SCAN_NOT_FOUND",
                "The competitor scan record no longer exists.",
                retryable=False,
            )
        if scan.status == ScanStatus.FAILED:
            raise JobExecutionFailure(
                scan.error_code or "COMPETITOR_SCAN_FAILED",
                scan.error_message or "The competitor scan failed.",
                retryable=True,
            )
        status_value = (
            BackgroundJobStatus.PARTIALLY_SUCCEEDED
            if scan.status == ScanStatus.PARTIALLY_SUCCEEDED
            else BackgroundJobStatus.SUCCEEDED
        )
        indexing_summary: dict[str, int] = {}
        if self._evidence_scheduler is not None:
            try:
                indexing_summary = await self._evidence_scheduler.schedule_scan(
                    organization_id=job.organization_id,
                    company_profile_id=job.company_profile_id,
                    scan_id=scan.id,
                    competitor_id=scan.competitor_id,
                    created_by_user_id=job.created_by_user_id,
                )
            except Exception:
                # Semantic indexing is an optional post-scan projection. Canonical
                # snapshots and signals remain successful when its queue is degraded.
                indexing_summary = {"indexing_enqueue_failed": 1}
        return JobExecutionResult(
            status=status_value,
            message="Competitor scan completed.",
            result_summary={
                "domain_status": scan.status.value,
                "signal_count": scan.signal_count,
                "competitor_id": scan.competitor_id,
                **indexing_summary,
            },
        )


class DocumentIngestionJobHandler:
    job_type = JobType.DOCUMENT_INGESTION

    def __init__(self, runner: JobRunner, persistence: Persistence) -> None:
        self._runner = runner
        self._persistence = persistence

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        domain = await self._persistence.document_ingestion_jobs.get_by_id(
            job.organization_id, job.domain_resource_id
        )
        if domain is None:
            raise JobExecutionFailure(
                "DOCUMENT_JOB_NOT_FOUND",
                "The document processing record no longer exists.",
                retryable=False,
            )
        document = await self._persistence.company_documents.get_by_id(
            job.organization_id, domain.document_id
        )
        if document is None or document.company_profile_id != domain.company_profile_id:
            raise JobExecutionFailure(
                "DOCUMENT_NOT_FOUND",
                "The document no longer exists in this workspace.",
                retryable=False,
            )
        await context.report_progress(job, 2, "document_processing", "Preparing private PDF.")
        await self._runner.run(job_id=job.domain_resource_id, organization_id=job.organization_id)
        completed = await self._persistence.document_ingestion_jobs.get_by_id(
            job.organization_id, job.domain_resource_id
        )
        if completed is None or completed.status == DocumentJobStatus.FAILED:
            raise JobExecutionFailure(
                (completed.error_code or "DOCUMENT_PROCESSING_FAILED")
                if completed
                else "DOCUMENT_JOB_NOT_FOUND",
                (
                    completed.error_message
                    if completed
                    else "The document processing record no longer exists."
                )
                or "Document processing failed.",
                retryable=(
                    completed is not None
                    and (completed.error_code or "")
                    in {
                        "DOCUMENT_STORAGE_UNAVAILABLE",
                        "EMBEDDING_PROVIDER_UNAVAILABLE",
                        "VECTOR_STORE_UNAVAILABLE",
                    }
                ),
            )
        return JobExecutionResult(
            message="Private document processing completed.",
            result_summary={
                "document_id": completed.document_id,
                "chunk_count": completed.chunk_count,
                "vector_count": completed.vector_count,
            },
        )


class CleanupJobHandler:
    def __init__(
        self,
        job_type: JobType,
        cleanup: Callable[[BackgroundJob, JobExecutionContext], Awaitable[Mapping[str, object]]],
    ) -> None:
        self.job_type = job_type
        self._cleanup = cleanup

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        if await context.cancelled(job):
            return JobExecutionResult(
                status=BackgroundJobStatus.CANCELLED,
                stage="cancelled",
                message="Cleanup was cancelled before a destructive operation.",
            )
        summary = await self._cleanup(job, context)
        return JobExecutionResult(message="Cleanup completed.", result_summary=dict(summary))


class RetentionCleanupJobHandler:
    job_type = JobType.RETENTION_CLEANUP

    def __init__(
        self,
        persistence: Persistence,
        queue: BackgroundJobQueue,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._queue = queue
        self._settings = settings

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        now = context.clock.now()
        batch_size = self._settings.job_recovery_batch_size
        upload_expiry = now - timedelta(
            seconds=max(3600, self._settings.document_upload_url_ttl_seconds * 4)
        )
        expired_documents = await self._persistence.company_documents.list_expired_pending(
            job.organization_id,
            before=upload_expiry,
            limit=batch_size,
        )
        storage_jobs = 0
        for document in expired_documents:
            if await context.cancelled(job):
                return JobExecutionResult(
                    status=BackgroundJobStatus.CANCELLED,
                    stage="cancelled",
                    message="Retention cleanup stopped at a safe checkpoint.",
                )
            canonical = await self._persistence.company_documents.get_by_id(
                job.organization_id,
                document.id,
                include_deleted=True,
            )
            if (
                canonical is None
                or canonical.status != DocumentStatus.PENDING_UPLOAD
                or canonical.created_at >= upload_expiry
            ):
                continue
            await self._persistence.company_documents.update(
                job.organization_id,
                canonical.id,
                {
                    "status": DocumentStatus.DELETED,
                    "stage": DocumentStage.DELETED,
                    "deleted_at": now,
                    "error_code": "UPLOAD_EXPIRED",
                    "error_message": "The pending upload expired before completion.",
                },
            )
            await self._queue.enqueue(
                organization_id=job.organization_id,
                company_profile_id=canonical.company_profile_id,
                job_type=JobType.DOCUMENT_STORAGE_CLEANUP,
                domain_resource_type="company_document",
                domain_resource_id=canonical.id,
                deduplication_key=f"expired-upload-storage:{canonical.id}",
                created_by_user_id=canonical.uploaded_by_user_id,
                priority=-10,
                enforce_backpressure=False,
                deduplicate_terminal=True,
            )
            storage_jobs += 1

        terminal_jobs = await self._persistence.background_jobs.cleanup_terminal(
            job.organization_id,
            succeeded_before=now - timedelta(days=self._settings.job_cleanup_retention_days),
            failed_before=now - timedelta(days=self._settings.dead_letter_retention_days),
            limit=batch_size,
        )
        notifications = await self._persistence.notifications.cleanup_expired(
            job.organization_id,
            before=now - timedelta(days=self._settings.notification_retention_days),
            limit=batch_size,
        )
        service_instances = await self._persistence.service_instances.cleanup_expired(
            before=now
            - timedelta(
                seconds=max(
                    self._settings.worker_heartbeat_ttl_seconds,
                    self._settings.scheduler_heartbeat_ttl_seconds,
                )
                * 2
            ),
            limit=batch_size,
        )
        return JobExecutionResult(
            message="Retention cleanup completed.",
            result_summary={
                "expired_uploads": len(expired_documents),
                "storage_cleanup_jobs": storage_jobs,
                "terminal_jobs_deleted": terminal_jobs,
                "notifications_deleted": notifications,
                "service_instances_deleted": service_instances,
            },
        )


class BackgroundJobService:
    def __init__(
        self,
        persistence: Persistence,
        queue: BackgroundJobQueue,
        clock: Clock | None = None,
    ) -> None:
        self._persistence = persistence
        self._queue = queue
        self._clock = clock or SystemClock()

    async def list(
        self,
        context: AuthenticatedContext,
        *,
        job_status: BackgroundJobStatus | None,
        cursor: str | None,
        limit: int,
    ) -> BackgroundJobPage:
        items = await self._persistence.background_jobs.list(
            context.organization_id,
            status=job_status,
            cursor=cursor,
            limit=limit + 1,
        )
        return BackgroundJobPage(
            items=items[:limit],
            next_cursor=items[limit - 1].id if len(items) > limit else None,
        )

    async def get(self, context: AuthenticatedContext, job_id: str) -> BackgroundJob:
        job = await self._persistence.background_jobs.get_by_id(context.organization_id, job_id)
        if job is None:
            raise self._not_found()
        return job

    async def cancel(self, context: AuthenticatedContext, job_id: str) -> BackgroundJob:
        job = await self._persistence.background_jobs.request_cancellation(
            context.organization_id, job_id, self._clock.now()
        )
        if job is None:
            existing = await self._persistence.background_jobs.get_by_id(
                context.organization_id, job_id
            )
            if existing is None:
                raise self._not_found()
            raise AppError(
                code="JOB_NOT_CANCELLABLE",
                message="This job can no longer be cancelled.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if job.status == BackgroundJobStatus.CANCELLED:
            await mark_domain_cancelled(self._persistence, job)
        return job

    async def retry(self, context: AuthenticatedContext, job_id: str) -> RetryJobResponse:
        previous = await self.get(context, job_id)
        if (
            previous.status
            not in {
                BackgroundJobStatus.FAILED,
                BackgroundJobStatus.DEAD_LETTERED,
            }
            or previous.retryable is False
        ):
            raise AppError(
                code="JOB_NOT_RETRYABLE",
                message="This job cannot be retried.",
                status_code=status.HTTP_409_CONFLICT,
            )
        retried = await self._queue.enqueue(
            organization_id=context.organization_id,
            company_profile_id=previous.company_profile_id,
            job_type=previous.job_type,
            domain_resource_type=previous.domain_resource_type,
            domain_resource_id=previous.domain_resource_id,
            deduplication_key=f"retry:{previous.id}:{self._clock.now().isoformat()}",
            payload=previous.payload,
            created_by_user_id=context.user_id,
        )
        return RetryJobResponse(job=retried, retried_from_job_id=previous.id)

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            code="BACKGROUND_JOB_NOT_FOUND",
            message="The background job was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class NotificationEmitter:
    def __init__(
        self, persistence: Persistence, settings: Settings, clock: Clock | None = None
    ) -> None:
        self._persistence = persistence
        self._settings = settings
        self._clock = clock or SystemClock()

    async def for_job_result(self, job: BackgroundJob, *, final_failure: bool = False) -> None:
        from app.domain.background_jobs import Notification
        from app.domain.onboarding import new_id

        notification_type: NotificationType | None = None
        severity = NotificationSeverity.INFO
        title = ""
        message = ""
        action_url: str | None = None
        if (
            job.job_type in {JobType.COMPETITOR_BASELINE_SCAN, JobType.COMPETITOR_RESCAN}
            and not final_failure
            and int(job.result_summary.get("signal_count", 0)) > 0
        ):
            count = int(job.result_summary["signal_count"])
            notification_type = NotificationType.NEW_COMPETITOR_SIGNALS
            title = f"{count} new competitor changes detected"
            message = "Review the new evidence-backed signals."
            competitor_id = str(job.result_summary.get("competitor_id", ""))
            action_url = f"/competitors/{competitor_id}" if competitor_id else "/competitors"
        elif final_failure:
            severity = NotificationSeverity.ERROR
            if job.job_type in {
                JobType.COMPETITOR_BASELINE_SCAN,
                JobType.COMPETITOR_RESCAN,
            }:
                notification_type = NotificationType.SCAN_FAILED
                title = "Competitor scan needs attention"
                action_url = "/competitors"
            elif job.job_type in {
                JobType.COMPANY_INITIALIZATION,
                JobType.COMPANY_INTELLIGENCE_REBUILD,
            }:
                notification_type = NotificationType.INITIALIZATION_FAILED
                title = "Company intelligence setup failed"
                action_url = "/onboarding/progress"
            elif job.job_type == JobType.DOCUMENT_INGESTION:
                notification_type = NotificationType.DOCUMENT_PROCESSING_FAILED
                title = "A company document could not be processed"
                action_url = "/settings"
            elif job.job_type in {
                JobType.COMPETITOR_SNAPSHOT_INDEXING,
                JobType.COMPETITOR_SIGNAL_INDEXING,
            }:
                notification_type = NotificationType.COMPETITOR_EVIDENCE_INDEXING_FAILED
                title = "Competitor evidence indexing needs attention"
                action_url = "/intelligence"
            elif job.job_type == JobType.COMPETITOR_INTELLIGENCE_REINDEX:
                notification_type = NotificationType.COMPETITOR_INTELLIGENCE_REINDEX_FAILED
                title = "Intelligence reindexing failed"
                action_url = "/intelligence"
            elif job.job_type == JobType.STRATEGIC_ANALYSIS:
                notification_type = NotificationType.STRATEGIC_REPORT_FAILED
                title = "Strategic analysis needs attention"
                action_url = "/strategy"
            else:
                notification_type = NotificationType.JOB_DEAD_LETTERED
                title = "A background job needs attention"
                action_url = "/settings"
            message = job.error_message or "Open Stratix to review recovery options."
        elif job.job_type == JobType.COMPETITOR_INTELLIGENCE_REINDEX:
            notification_type = NotificationType.COMPETITOR_INTELLIGENCE_REINDEX_COMPLETED
            title = "Competitor intelligence is up to date"
            message = "Workspace evidence reindexing completed."
            action_url = "/intelligence"
        elif job.job_type == JobType.STRATEGIC_ANALYSIS:
            partial = bool(job.result_summary.get("deterministic_fallback"))
            notification_type = (
                NotificationType.STRATEGIC_REPORT_PARTIAL
                if partial
                else NotificationType.STRATEGIC_REPORT_READY
            )
            severity = NotificationSeverity.WARNING if partial else NotificationSeverity.INFO
            title = "Verified evidence summary ready" if partial else "Strategic report ready"
            message = (
                "The model result was unavailable; Stratix retained a verified evidence summary."
                if partial
                else "Your evidence-grounded strategic analysis is ready to review."
            )
            report_id = str(job.result_summary.get("report_id", ""))
            action_url = f"/strategy?report={report_id}" if report_id else "/strategy"
        if notification_type is None:
            return
        now = self._clock.now()
        notification = Notification(
            id=new_id(),
            organization_id=job.organization_id,
            user_id=job.created_by_user_id,
            company_profile_id=job.company_profile_id,
            type=notification_type,
            severity=severity,
            title=title,
            message=message,
            resource_type=job.domain_resource_type,
            resource_id=job.domain_resource_id,
            action_url=action_url,
            deduplication_key=f"{notification_type.value}:{job.id}",
            created_at=now,
            expires_at=now + timedelta(days=self._settings.notification_retention_days),
        )
        try:
            await self._persistence.notifications.create(notification)
        except RepositoryConflictError:
            return


class DurableWorker:
    def __init__(
        self,
        *,
        persistence: Persistence,
        handlers: Mapping[JobType, BackgroundJobHandler],
        settings: Settings,
        worker_id: str,
        clock: Clock | None = None,
        jitter_source: Callable[[], float] = random.random,
    ) -> None:
        self._persistence = persistence
        self._handlers = dict(handlers)
        self._settings = settings
        self.worker_id = worker_id
        self._clock = clock or SystemClock()
        self._jitter_source = jitter_source
        self._shutdown = asyncio.Event()
        self._active: set[asyncio.Task[None]] = set()
        self._global = asyncio.Semaphore(settings.worker_max_concurrency)
        self._job_type_semaphores = {
            JobType.COMPANY_INITIALIZATION: asyncio.Semaphore(
                settings.company_initialization_max_concurrency
            ),
            JobType.COMPANY_INTELLIGENCE_REBUILD: asyncio.Semaphore(
                settings.company_initialization_max_concurrency
            ),
            JobType.COMPETITOR_BASELINE_SCAN: asyncio.Semaphore(
                settings.competitor_scan_max_concurrency
            ),
            JobType.COMPETITOR_RESCAN: asyncio.Semaphore(settings.competitor_scan_max_concurrency),
            JobType.DOCUMENT_INGESTION: asyncio.Semaphore(
                settings.document_ingestion_max_concurrency
            ),
            JobType.PINECONE_CLEANUP: asyncio.Semaphore(settings.cleanup_job_max_concurrency),
            JobType.DOCUMENT_STORAGE_CLEANUP: asyncio.Semaphore(
                settings.cleanup_job_max_concurrency
            ),
            JobType.RETENTION_CLEANUP: asyncio.Semaphore(settings.cleanup_job_max_concurrency),
            JobType.COMPETITOR_SNAPSHOT_INDEXING: asyncio.Semaphore(
                settings.competitor_evidence_max_concurrency
            ),
            JobType.COMPETITOR_SIGNAL_INDEXING: asyncio.Semaphore(
                settings.competitor_evidence_max_concurrency
            ),
            JobType.COMPETITOR_INTELLIGENCE_REINDEX: asyncio.Semaphore(1),
            JobType.COMPETITOR_EVIDENCE_CLEANUP: asyncio.Semaphore(
                settings.cleanup_job_max_concurrency
            ),
            JobType.STRATEGIC_ANALYSIS: asyncio.Semaphore(
                settings.strategic_agent_max_concurrent_runs_global
            ),
        }
        self._organization_semaphores: dict[str, asyncio.Semaphore] = {}
        self._last_organization_id: str | None = None
        self._notifications = NotificationEmitter(persistence, settings, self._clock)

    async def recover(self) -> list[BackgroundJob]:
        recovered = await self._persistence.background_jobs.recover_expired_leases(
            now=self._clock.now(), limit=self._settings.job_recovery_batch_size
        )
        for job in recovered:
            await self._reconcile_recovered_domain(job)
            if job.status == BackgroundJobStatus.DEAD_LETTERED:
                await self._notifications.for_job_result(job, final_failure=True)
        return recovered

    async def run_once(self) -> bool:
        if self._shutdown.is_set() or len(self._active) >= self._settings.worker_max_concurrency:
            return False
        supported = set(self._handlers)
        now = self._clock.now()
        job = await self._persistence.background_jobs.claim_next(
            worker_id=self.worker_id,
            supported_job_types=supported,
            now=now,
            lease_duration=timedelta(seconds=self._settings.job_lease_duration_seconds),
            excluded_organization_id=self._last_organization_id,
        )
        if job is None and self._last_organization_id is not None:
            job = await self._persistence.background_jobs.claim_next(
                worker_id=self.worker_id,
                supported_job_types=supported,
                now=now,
                lease_duration=timedelta(seconds=self._settings.job_lease_duration_seconds),
            )
        if job is None:
            return False
        self._last_organization_id = job.organization_id
        task = asyncio.create_task(self._execute(job))
        self._active.add(task)
        task.add_done_callback(self._active.discard)
        return True

    async def serve(self) -> None:
        await self.recover()
        last_recovery_at = monotonic()
        recovery_interval = max(
            5.0,
            min(60.0, self._settings.job_lease_duration_seconds / 2),
        )
        idle_delay = self._settings.worker_poll_interval_seconds
        while not self._shutdown.is_set():
            if monotonic() - last_recovery_at >= recovery_interval:
                await self.recover()
                last_recovery_at = monotonic()
            claimed = await self.run_once()
            if claimed:
                idle_delay = self._settings.worker_poll_interval_seconds
                await asyncio.sleep(0)
            else:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._shutdown.wait(), timeout=idle_delay)
                idle_delay = min(
                    self._settings.worker_idle_backoff_max_seconds,
                    max(self._settings.worker_poll_interval_seconds, idle_delay * 1.5),
                )

    async def shutdown(self) -> None:
        self._shutdown.set()
        if not self._active:
            return
        done, pending = await asyncio.wait(
            self._active,
            timeout=self._settings.worker_graceful_shutdown_seconds,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            with suppress(asyncio.CancelledError):
                task.result()

    async def drain(self) -> None:
        """Wait for currently claimed work without claiming additional jobs."""
        if self._active:
            await asyncio.gather(*tuple(self._active), return_exceptions=True)

    async def _execute(self, job: BackgroundJob) -> None:
        type_semaphore = self._job_type_semaphores.get(job.job_type, asyncio.Semaphore(1))
        organization_semaphore = self._organization_semaphores.setdefault(
            job.organization_id,
            asyncio.Semaphore(self._settings.max_running_jobs_per_organization),
        )
        async with self._global, type_semaphore, organization_semaphore:
            running = await self._persistence.background_jobs.mark_running(
                job_id=job.id, worker_id=self.worker_id, now=self._clock.now()
            )
            if running is None:
                return
            lost_lease = asyncio.Event()
            heartbeat = asyncio.create_task(self._heartbeat(running, lost_lease))
            context = JobExecutionContext(
                persistence=self._persistence,
                clock=self._clock,
                worker_id=self.worker_id,
                lease_lost=lost_lease,
                shutdown_requested=self._shutdown,
                progress_min_interval_seconds=(
                    self._settings.job_progress_update_min_interval_seconds
                ),
                progress_min_percent_delta=(self._settings.job_progress_update_min_percent_delta),
            )
            started = monotonic()
            logger.info(
                "background_job_attempt_started",
                job_id=running.id,
                job_type=running.job_type.value,
                organization_id=running.organization_id,
                domain_resource_id=running.domain_resource_id,
                attempt=running.attempt_count,
                worker_id=self.worker_id,
            )
            try:
                handler = self._handlers.get(running.job_type)
                if handler is None:
                    raise JobExecutionFailure(
                        "JOB_TYPE_UNSUPPORTED",
                        "No worker handler supports this job type.",
                        retryable=False,
                    )
                result = await handler.execute(job=running, context=context)
                current = await self._persistence.background_jobs.get_by_id(
                    running.organization_id, running.id
                )
                if lost_lease.is_set() or current is None or current.worker_id != self.worker_id:
                    return
                if current.status == BackgroundJobStatus.CANCEL_REQUESTED:
                    cancelled = await self._persistence.background_jobs.mark_cancelled(
                        job_id=running.id,
                        worker_id=self.worker_id,
                        now=self._clock.now(),
                    )
                    if cancelled:
                        await mark_domain_cancelled(self._persistence, cancelled)
                    return
                if result.status == BackgroundJobStatus.CANCELLED:
                    cancelled = await self._persistence.background_jobs.mark_cancelled(
                        job_id=running.id,
                        worker_id=self.worker_id,
                        now=self._clock.now(),
                    )
                    if cancelled:
                        await mark_domain_cancelled(self._persistence, cancelled)
                    return
                completed = await self._persistence.background_jobs.mark_succeeded(
                    job_id=running.id,
                    worker_id=self.worker_id,
                    now=self._clock.now(),
                    status=result.status,
                    stage=result.stage,
                    message=result.message,
                    result_summary=result.result_summary,
                )
                if completed:
                    await self._update_monitoring_outcome(completed, succeeded=True)
                    await self._notifications.for_job_result(completed)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                if lost_lease.is_set():
                    return
                code, message, retryable = classify_job_error(exc)
                now = self._clock.now()
                if retryable and running.attempt_count < running.max_attempts:
                    delay = retry_delay_seconds(
                        attempt_count=running.attempt_count,
                        base_seconds=self._settings.job_retry_base_seconds,
                        maximum_seconds=self._settings.job_retry_max_seconds,
                        jitter_ratio=self._settings.job_retry_jitter_ratio,
                        jitter_source=self._jitter_source,
                    )
                    await self._persistence.background_jobs.schedule_retry(
                        job_id=running.id,
                        worker_id=self.worker_id,
                        now=now,
                        available_at=now + timedelta(seconds=delay),
                        error_code=code,
                        error_message=message,
                        error_class=type(exc).__name__,
                    )
                elif code == "JOB_TYPE_UNSUPPORTED" or retryable:
                    dead = await self._persistence.background_jobs.mark_dead_lettered(
                        job_id=running.id,
                        worker_id=self.worker_id,
                        now=now,
                        error_code=code,
                        error_message=message,
                        error_class=type(exc).__name__,
                    )
                    if dead:
                        await self._update_monitoring_outcome(dead, succeeded=False)
                        await self._notifications.for_job_result(dead, final_failure=True)
                else:
                    failed = await self._persistence.background_jobs.mark_failed(
                        job_id=running.id,
                        worker_id=self.worker_id,
                        now=now,
                        error_code=code,
                        error_message=message,
                        error_class=type(exc).__name__,
                    )
                    if failed:
                        await self._update_monitoring_outcome(failed, succeeded=False)
                        await self._notifications.for_job_result(failed, final_failure=True)
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                logger.info(
                    "background_job_attempt_finished",
                    job_id=running.id,
                    job_type=running.job_type.value,
                    organization_id=running.organization_id,
                    domain_resource_id=running.domain_resource_id,
                    attempt=running.attempt_count,
                    worker_id=self.worker_id,
                    duration_ms=round((monotonic() - started) * 1000, 2),
                )

    async def _heartbeat(self, job: BackgroundJob, lost_lease: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(self._settings.job_heartbeat_interval_seconds)
            renewed = await self._persistence.background_jobs.renew_lease(
                job_id=job.id,
                worker_id=self.worker_id,
                now=self._clock.now(),
                lease_duration=timedelta(seconds=self._settings.job_lease_duration_seconds),
            )
            if not renewed:
                lost_lease.set()
                return

    async def _update_monitoring_outcome(self, job: BackgroundJob, *, succeeded: bool) -> None:
        if job.job_type not in {
            JobType.COMPETITOR_BASELINE_SCAN,
            JobType.COMPETITOR_RESCAN,
        }:
            return
        competitor_id = str(job.payload.get("competitor_id", ""))
        if not competitor_id:
            return
        schedule = await self._persistence.monitoring_schedules.get_for_competitor(
            job.organization_id, competitor_id
        )
        if schedule is None:
            return
        await self._persistence.monitoring_schedules.update(
            job.organization_id,
            schedule.id,
            {
                "last_success_at" if succeeded else "last_failure_at": self._clock.now(),
                "updated_at": self._clock.now(),
            },
        )

    async def _reconcile_recovered_domain(self, job: BackgroundJob) -> None:
        retrying = job.status == BackgroundJobStatus.RETRY_SCHEDULED
        message = (
            "The job was recovered and is waiting to retry."
            if retrying
            else "The job stopped after its worker lease expired."
        )
        if job.job_type in {
            JobType.COMPANY_INITIALIZATION,
            JobType.COMPANY_INTELLIGENCE_REBUILD,
        }:
            if retrying:
                await self._persistence.initialization_jobs.reset_for_retry(
                    job.organization_id, job.domain_resource_id, message
                )
            else:
                await self._persistence.initialization_jobs.mark_failed(
                    job.organization_id,
                    job.domain_resource_id,
                    "JOB_LEASE_EXPIRED",
                    message,
                )
        elif job.job_type in {
            JobType.COMPETITOR_BASELINE_SCAN,
            JobType.COMPETITOR_RESCAN,
        }:
            if retrying:
                await self._persistence.competitor_scans.reset_for_retry(
                    job.organization_id, job.domain_resource_id, message
                )
            else:
                await self._persistence.competitor_scans.finalize(
                    job.organization_id,
                    job.domain_resource_id,
                    ScanStatus.FAILED,
                    error_code="JOB_LEASE_EXPIRED",
                    error_message=message,
                )
        elif job.job_type == JobType.DOCUMENT_INGESTION:
            if retrying:
                await self._persistence.document_ingestion_jobs.reset_for_retry(
                    job.organization_id, job.domain_resource_id, message
                )
            else:
                await self._persistence.document_ingestion_jobs.finalize(
                    job.organization_id,
                    job.domain_resource_id,
                    DocumentJobStatus.FAILED,
                    error_code="JOB_LEASE_EXPIRED",
                    error_message=message,
                )
        elif job.job_type == JobType.STRATEGIC_ANALYSIS:
            await self._persistence.strategy_runs.update(
                job.organization_id,
                job.domain_resource_id,
                {
                    "status": (StrategyRunStatus.QUEUED if retrying else StrategyRunStatus.FAILED),
                    "stage": "awaiting_retry" if retrying else "failed",
                    "message": message,
                    "error_code": None if retrying else "STRATEGIC_AGENT_LEASE_LOST",
                    "error_message": None if retrying else message,
                    **({} if retrying else {"completed_at": self._clock.now()}),
                },
            )
