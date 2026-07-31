from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.domain.background_jobs import (
    BackgroundJob,
    JobExecutionResult,
    JobType,
)
from app.domain.discovery import CompetitorStatus
from app.domain.intelligence_evidence import (
    EvidenceSourceKind,
    IntelligenceReindexRequest,
    IntelligenceReindexResponse,
)
from app.repositories.interfaces import Persistence
from app.services.background_jobs import (
    BackgroundJobQueue,
    JobExecutionContext,
    JobExecutionFailure,
)
from app.services.embeddings import EmbeddingError
from app.services.intelligence import CompetitorEvidenceIndexingService
from app.services.vector_store import VectorStoreError


class EvidenceIndexingScheduler:
    def __init__(
        self,
        persistence: Persistence,
        queue: BackgroundJobQueue,
    ) -> None:
        self._persistence = persistence
        self._queue = queue

    async def schedule_scan(
        self,
        *,
        organization_id: str,
        company_profile_id: str | None,
        scan_id: str,
        competitor_id: str,
        created_by_user_id: str | None,
    ) -> dict[str, int]:
        snapshot_jobs = signal_jobs = 0
        snapshots = await self._persistence.competitor_snapshots.list_for_scan(
            organization_id, scan_id, limit=500
        )
        for snapshot in snapshots:
            await self._queue.enqueue(
                organization_id=organization_id,
                company_profile_id=company_profile_id,
                job_type=JobType.COMPETITOR_SNAPSHOT_INDEXING,
                domain_resource_type="competitor_snapshot",
                domain_resource_id=snapshot.id,
                deduplication_key=f"evidence:snapshot:{snapshot.id}",
                payload={"competitor_id": competitor_id, "scan_id": scan_id},
                created_by_user_id=created_by_user_id,
                priority=-5,
                enforce_backpressure=False,
                deduplicate_terminal=True,
            )
            snapshot_jobs += 1
        signals = await self._persistence.competitor_signals.list_for_competitor(
            organization_id,
            competitor_id,
            signal_type=None,
            signal_status=None,
            minimum_importance=0,
            source_id=None,
            observed_after=None,
            observed_before=None,
            scan_id=scan_id,
            cursor=None,
            limit=500,
        )
        for signal in signals:
            await self._queue.enqueue(
                organization_id=organization_id,
                company_profile_id=company_profile_id,
                job_type=JobType.COMPETITOR_SIGNAL_INDEXING,
                domain_resource_type="competitor_signal",
                domain_resource_id=signal.id,
                deduplication_key=f"evidence:signal:{signal.id}",
                payload={"competitor_id": competitor_id, "scan_id": scan_id},
                created_by_user_id=created_by_user_id,
                priority=-5,
                enforce_backpressure=False,
                deduplicate_terminal=True,
            )
            signal_jobs += 1
        return {"snapshot_jobs": snapshot_jobs, "signal_jobs": signal_jobs}


class EvidenceIndexingJobHandler:
    def __init__(self, job_type: JobType, service: CompetitorEvidenceIndexingService) -> None:
        self.job_type = job_type
        self._service = service

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        await context.report_progress(job, 10, "preparing", "Preparing competitor evidence.")
        if await context.cancelled(job):
            raise JobExecutionFailure(
                "INTELLIGENCE_INDEXING_CANCELLED",
                "Evidence indexing was cancelled before processing.",
                retryable=False,
            )

        async def lease_is_current() -> bool:
            return not await context.cancelled(job)

        try:
            if self.job_type == JobType.COMPETITOR_SNAPSHOT_INDEXING:
                count = await self._service.index_snapshot(
                    job.organization_id,
                    job.domain_resource_id,
                    before_activation=lease_is_current,
                )
            elif self.job_type == JobType.COMPETITOR_SIGNAL_INDEXING:
                count = await self._service.index_signal(
                    job.organization_id,
                    job.domain_resource_id,
                    before_activation=lease_is_current,
                )
            else:
                raise AssertionError("Unsupported evidence indexing job")
        except AppError as exc:
            raise JobExecutionFailure(
                exc.code,
                exc.message,
                retryable=exc.status_code >= 500,
            ) from exc
        except VectorStoreError as exc:
            dimension_mismatch = exc.code == "VECTOR_INDEX_DIMENSION_MISMATCH"
            raise JobExecutionFailure(
                (
                    "INTELLIGENCE_VECTOR_VERSION_MISMATCH"
                    if dimension_mismatch
                    else "INTELLIGENCE_INDEXING_FAILED"
                ),
                "Competitor evidence could not be indexed.",
                retryable=not dimension_mismatch,
            ) from exc
        except EmbeddingError as exc:
            raise JobExecutionFailure(
                "INTELLIGENCE_INDEXING_FAILED",
                "Competitor evidence could not be indexed.",
                retryable=True,
            ) from exc
        await context.report_progress(job, 95, "activating", "Activating indexed evidence.")
        return JobExecutionResult(
            message="Competitor evidence indexing completed.",
            result_summary={"evidence_count": count},
        )


class IntelligenceReindexJobHandler:
    job_type = JobType.COMPETITOR_INTELLIGENCE_REINDEX

    def __init__(self, service: CompetitorEvidenceIndexingService) -> None:
        self._service = service

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        await context.report_progress(job, 5, "loading", "Loading canonical evidence sources.")
        competitor_ids = [
            item for item in str(job.payload.get("competitor_ids", "")).split(",") if item
        ]

        async def lease_is_current() -> bool:
            return not await context.cancelled(job)

        try:
            summary = await self._service.reindex_organization(
                job.organization_id,
                competitor_ids=competitor_ids or None,
                should_continue=lease_is_current,
            )
        except AppError as exc:
            raise JobExecutionFailure(
                exc.code,
                exc.message,
                retryable=exc.status_code >= 500,
            ) from exc
        if summary["failed_count"] and not summary["indexed_count"]:
            raise JobExecutionFailure(
                "INTELLIGENCE_INDEXING_FAILED",
                "No competitor evidence could be reindexed.",
                retryable=True,
            )
        return JobExecutionResult(
            message="Competitor intelligence reindex completed.",
            result_summary=summary,
        )


class EvidenceCleanupJobHandler:
    job_type = JobType.COMPETITOR_EVIDENCE_CLEANUP

    def __init__(self, service: CompetitorEvidenceIndexingService) -> None:
        self._service = service

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        if await context.cancelled(job):
            raise JobExecutionFailure(
                "INTELLIGENCE_INDEXING_CANCELLED",
                "Evidence cleanup was cancelled before changing canonical eligibility.",
                retryable=False,
            )
        kind = EvidenceSourceKind(str(job.payload.get("source_kind", "")))
        count = await self._service.cleanup_source(
            job.organization_id, kind, job.domain_resource_id
        )
        return JobExecutionResult(
            message="Competitor evidence cleanup completed.",
            result_summary={"deleted_vector_count": count},
        )


class IntelligenceReindexService:
    def __init__(self, persistence: Persistence, queue: BackgroundJobQueue) -> None:
        self._persistence = persistence
        self._queue = queue

    async def schedule(
        self,
        context: AuthenticatedContext,
        request: IntelligenceReindexRequest,
    ) -> IntelligenceReindexResponse:
        for competitor_id in request.competitor_ids:
            competitor = await self._persistence.competitors.get_by_id(
                context.organization_id,
                competitor_id,
            )
            if competitor is None or competitor.status != CompetitorStatus.ACTIVE:
                raise AppError(
                    code="INTELLIGENCE_FILTER_INVALID",
                    message="One or more competitor filters are invalid.",
                    status_code=400,
                )
        deduplication_key = f"intelligence-reindex:{context.organization_id}"
        active = await self._persistence.background_jobs.get_active_by_deduplication_key(
            context.organization_id, deduplication_key
        )
        if active is not None:
            raise AppError(
                code="INTELLIGENCE_REINDEX_ALREADY_ACTIVE",
                message="Workspace intelligence reindexing is already active.",
                status_code=409,
            )
        profile = await self._persistence.company_profiles.get_for_organization(
            context.organization_id
        )
        job = await self._queue.enqueue(
            organization_id=context.organization_id,
            company_profile_id=profile.id if profile else None,
            job_type=JobType.COMPETITOR_INTELLIGENCE_REINDEX,
            domain_resource_type="organization_intelligence",
            domain_resource_id=context.organization_id,
            deduplication_key=deduplication_key,
            payload={"competitor_ids": ",".join(request.competitor_ids)},
            created_by_user_id=context.user_id,
            priority=-10,
            enforce_backpressure=True,
        )
        return IntelligenceReindexResponse(job_id=job.id, status=job.status.value)
