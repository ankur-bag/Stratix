import asyncio
from typing import Protocol

from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.domain.discovery import BaselineStatus, CompetitorStatus, MonitoringStatus
from app.domain.onboarding import new_id, utc_now
from app.domain.scanning import (
    CompetitorScanJob,
    CompetitorScanPage,
    CompetitorScanRequest,
    CompetitorScanResponse,
    ScanStage,
    ScanStatus,
    ScanTrigger,
    ScanType,
)
from app.graphs.competitor_scan import CompetitorScanGraph
from app.repositories.interfaces import Persistence, RepositoryConflictError
from app.services.onboarding import IDEMPOTENCY_KEY_PATTERN


class CompetitorScanRunner(Protocol):
    async def run(self, *, scan_id: str, organization_id: str) -> None: ...


class CompetitorScanExecutor(Protocol):
    def schedule(self, *, scan_id: str, organization_id: str) -> None: ...
    async def close(self) -> None: ...


class InProcessCompetitorScanExecutor:
    """Run persisted competitor scans in non-durable process tasks."""

    def __init__(self, runner: CompetitorScanRunner) -> None:
        self._runner = runner
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, *, scan_id: str, organization_id: str) -> None:
        task = asyncio.create_task(
            self._runner.run(scan_id=scan_id, organization_id=organization_id)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()


class GraphCompetitorScanRunner:
    def __init__(self, persistence: Persistence, graph: CompetitorScanGraph) -> None:
        self._persistence = persistence
        self._graph = graph

    async def run(self, *, scan_id: str, organization_id: str) -> None:
        scan = await self._persistence.competitor_scans.get_by_id(organization_id, scan_id)
        if scan is None:
            return
        try:
            await self._graph.run(scan=scan)
        except Exception:
            await self._persistence.competitor_scans.finalize(
                organization_id,
                scan_id,
                ScanStatus.FAILED,
                error_code="COMPETITOR_SCAN_FAILED",
                error_message="The competitor scan could not be completed.",
            )


class CompetitorScanService:
    """Create idempotent scan jobs and expose organization-scoped progress."""

    def __init__(self, persistence: Persistence, executor: CompetitorScanExecutor) -> None:
        self._persistence = persistence
        self._executor = executor

    async def start(
        self,
        *,
        context: AuthenticatedContext,
        competitor_id: str,
        idempotency_key: str,
        request: CompetitorScanRequest,
        trigger: ScanTrigger = ScanTrigger.MANUAL,
    ) -> CompetitorScanResponse:
        key = idempotency_key.strip()
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
            raise AppError(
                code="INVALID_IDEMPOTENCY_KEY",
                message="A valid Idempotency-Key header is required.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        existing = await self._persistence.competitor_scans.get_by_idempotency_key(
            context.organization_id, key
        )
        if existing is not None:
            return CompetitorScanResponse(scan=existing)
        competitor = await self._persistence.competitors.get_by_id(
            context.organization_id, competitor_id
        )
        if competitor is None:
            raise self._not_found("COMPETITOR_NOT_FOUND", "The competitor was not found.")
        if competitor.status != CompetitorStatus.ACTIVE:
            raise AppError(
                code="COMPETITOR_NOT_ACTIVE",
                message="Only active competitors can be scanned.",
                status_code=status.HTTP_409_CONFLICT,
            )
        active = await self._persistence.competitor_scans.get_active_for_competitor(
            context.organization_id, competitor_id
        )
        if active is not None:
            raise AppError(
                code="COMPETITOR_SCAN_ALREADY_RUNNING",
                message="A competitor scan is already running.",
                status_code=status.HTTP_409_CONFLICT,
            )
        enabled = await self._persistence.competitor_sources.list_for_competitor(
            context.organization_id, competitor_id, enabled_only=True
        )
        if not enabled and not request.rediscover_sources:
            raise AppError(
                code="COMPETITOR_HAS_NO_ENABLED_SOURCES",
                message="The competitor has no enabled public sources.",
                status_code=status.HTTP_409_CONFLICT,
            )
        scan_type = (
            ScanType.BASELINE
            if competitor.baseline_status != BaselineStatus.READY
            else request.scan_type
        )
        now = utc_now()
        scan = CompetitorScanJob(
            id=new_id(),
            organization_id=context.organization_id,
            competitor_id=competitor_id,
            requested_by_user_id=context.user_id,
            scan_type=scan_type,
            trigger=trigger,
            status=ScanStatus.QUEUED,
            stage=ScanStage.QUEUED,
            progress_percent=0,
            message="Competitor scan is queued.",
            idempotency_key=key,
            rediscover_sources=request.rediscover_sources,
            created_at=now,
            updated_at=now,
        )
        try:
            await self._persistence.competitor_scans.create(scan)
        except RepositoryConflictError:
            existing = await self._persistence.competitor_scans.get_by_idempotency_key(
                context.organization_id, key
            )
            if existing is not None:
                return CompetitorScanResponse(scan=existing)
            raise AppError(
                code="COMPETITOR_SCAN_ALREADY_RUNNING",
                message="A competitor scan is already running.",
                status_code=status.HTTP_409_CONFLICT,
            ) from None
        await self._persistence.competitors.update(
            context.organization_id,
            competitor_id,
            {
                "monitoring_status": MonitoringStatus.INITIALIZING,
                "baseline_status": BaselineStatus.DISCOVERING_SOURCES
                if request.rediscover_sources
                else BaselineStatus.COLLECTING,
                "latest_scan_job_id": scan.id,
            },
        )
        try:
            self._executor.schedule(scan_id=scan.id, organization_id=context.organization_id)
        except Exception as exc:
            await self._persistence.competitor_scans.finalize(
                context.organization_id,
                scan.id,
                ScanStatus.FAILED,
                error_code="COMPETITOR_SCAN_FAILED",
                error_message="The competitor scan could not be scheduled.",
            )
            await self._persistence.competitors.update(
                context.organization_id,
                competitor_id,
                {
                    "monitoring_status": MonitoringStatus.FAILED,
                    "baseline_status": BaselineStatus.FAILED,
                },
            )
            raise AppError(
                code="COMPETITOR_SCAN_FAILED",
                message="The competitor scan could not be scheduled.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        return CompetitorScanResponse(scan=scan)

    async def start_auto(
        self, *, organization_id: str, competitor_id: str, requested_by_user_id: str
    ) -> None:
        try:
            await self.start(
                context=AuthenticatedContext(
                    user_id=requested_by_user_id, organization_id=organization_id
                ),
                competitor_id=competitor_id,
                idempotency_key=f"baseline:{competitor_id}",
                request=CompetitorScanRequest(scan_type=ScanType.BASELINE, rediscover_sources=True),
                trigger=ScanTrigger.APPROVAL,
            )
        except AppError:
            return

    async def get(self, context: AuthenticatedContext, scan_id: str) -> CompetitorScanJob:
        scan = await self._persistence.competitor_scans.get_by_id(context.organization_id, scan_id)
        if scan is None:
            raise self._not_found("COMPETITOR_SCAN_NOT_FOUND", "The competitor scan was not found.")
        return scan

    async def list(
        self,
        context: AuthenticatedContext,
        competitor_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> CompetitorScanPage:
        competitor = await self._persistence.competitors.get_by_id(
            context.organization_id, competitor_id
        )
        if competitor is None:
            raise self._not_found("COMPETITOR_NOT_FOUND", "The competitor was not found.")
        items = await self._persistence.competitor_scans.list_for_competitor(
            context.organization_id,
            competitor_id,
            cursor=cursor,
            limit=limit + 1,
        )
        return CompetitorScanPage(
            items=items[:limit], next_cursor=items[limit - 1].id if len(items) > limit else None
        )

    @staticmethod
    def _not_found(code: str, message: str) -> AppError:
        return AppError(code=code, message=message, status_code=status.HTTP_404_NOT_FOUND)
