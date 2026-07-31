import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.background_jobs import (
    JobType,
    MonitoringCadence,
    MonitoringSchedule,
    MonitoringScheduleUpdate,
)
from app.domain.onboarding import new_id
from app.domain.scanning import CompetitorScanRequest, CompetitorScanResponse, ScanTrigger, ScanType
from app.repositories.interfaces import Persistence, RepositoryConflictError
from app.services.background_jobs import BackgroundJobQueue, JobExecutionFailure
from app.services.scanning import CompetitorScanService

logger = get_logger(__name__)


def next_run_for(
    cadence: MonitoringCadence,
    *,
    after: datetime,
    timezone_name: str,
    minimum_interval_hours: int,
) -> datetime | None:
    if cadence == MonitoringCadence.MANUAL:
        return None
    if timezone_name == "UTC":
        selected_timezone: tzinfo = UTC
    else:
        try:
            selected_timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown monitoring timezone") from exc
    days = {
        MonitoringCadence.DAILY: 1,
        MonitoringCadence.EVERY_3_DAYS: 3,
        MonitoringCadence.WEEKLY: 7,
    }[cadence]
    local = after.astimezone(selected_timezone)
    candidate = (local + timedelta(days=days)).astimezone(after.tzinfo or UTC)
    earliest = after + timedelta(hours=minimum_interval_hours)
    return max(candidate, earliest)


class MonitoringScheduleService:
    def __init__(
        self,
        persistence: Persistence,
        scan_service: CompetitorScanService,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self._persistence = persistence
        self._scan_service = scan_service
        self._settings = settings
        self._clock = clock or SystemClock()

    async def ensure_default(
        self,
        *,
        organization_id: str,
        company_profile_id: str,
        competitor_id: str,
        created_by_user_id: str,
    ) -> MonitoringSchedule:
        existing = await self._persistence.monitoring_schedules.get_for_competitor(
            organization_id, competitor_id
        )
        if existing:
            return existing
        now = self._clock.now()
        cadence = MonitoringCadence(self._settings.monitoring_default_cadence)
        schedule = MonitoringSchedule(
            id=new_id(),
            organization_id=organization_id,
            company_profile_id=company_profile_id,
            competitor_id=competitor_id,
            cadence=cadence,
            is_enabled=cadence != MonitoringCadence.MANUAL,
            next_run_at=next_run_for(
                cadence,
                after=now,
                timezone_name="UTC",
                minimum_interval_hours=self._settings.monitoring_min_interval_hours,
            ),
            created_by_user_id=created_by_user_id,
            created_at=now,
            updated_at=now,
        )
        try:
            return await self._persistence.monitoring_schedules.create(schedule)
        except RepositoryConflictError:
            existing = await self._persistence.monitoring_schedules.get_for_competitor(
                organization_id, competitor_id
            )
            if existing:
                return existing
            raise

    async def get(self, context: AuthenticatedContext, competitor_id: str) -> MonitoringSchedule:
        await self._require_competitor(context.organization_id, competitor_id)
        schedule = await self._persistence.monitoring_schedules.get_for_competitor(
            context.organization_id, competitor_id
        )
        if schedule is None:
            company = await self._persistence.company_profiles.get_for_organization(
                context.organization_id
            )
            if company is None:
                raise self._not_found()
            schedule = await self.ensure_default(
                organization_id=context.organization_id,
                company_profile_id=company.id,
                competitor_id=competitor_id,
                created_by_user_id=context.user_id,
            )
        return schedule

    async def update(
        self,
        context: AuthenticatedContext,
        competitor_id: str,
        request: MonitoringScheduleUpdate,
    ) -> MonitoringSchedule:
        schedule = await self.get(context, competitor_id)
        try:
            if request.timezone != "UTC":
                ZoneInfo(request.timezone)
        except ZoneInfoNotFoundError as exc:
            raise AppError(
                code="MONITORING_TIMEZONE_INVALID",
                message="Select a valid timezone.",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            ) from exc
        now = self._clock.now()
        enabled = request.is_enabled and request.cadence != MonitoringCadence.MANUAL
        updated = await self._persistence.monitoring_schedules.update(
            context.organization_id,
            schedule.id,
            {
                "is_enabled": enabled,
                "cadence": request.cadence,
                "timezone": request.timezone,
                "next_run_at": (
                    next_run_for(
                        request.cadence,
                        after=now,
                        timezone_name=request.timezone,
                        minimum_interval_hours=self._settings.monitoring_min_interval_hours,
                    )
                    if enabled
                    else None
                ),
                "degraded_reason": None,
                "updated_at": now,
            },
        )
        assert updated is not None
        if not enabled:
            from app.domain.background_jobs import (
                Notification,
                NotificationSeverity,
                NotificationType,
            )

            with suppress(RepositoryConflictError):
                await self._persistence.notifications.create(
                    Notification(
                        id=new_id(),
                        organization_id=context.organization_id,
                        user_id=context.user_id,
                        company_profile_id=schedule.company_profile_id,
                        type=NotificationType.MONITORING_PAUSED,
                        severity=NotificationSeverity.WARNING,
                        title="Competitor monitoring paused",
                        message="Automatic scans are paused until monitoring is enabled again.",
                        resource_type="competitor",
                        resource_id=competitor_id,
                        action_url=f"/competitors/{competitor_id}",
                        deduplication_key=f"monitoring-paused:{schedule.id}:{now.date()}",
                        created_at=now,
                        expires_at=now + timedelta(days=self._settings.notification_retention_days),
                    )
                )
        return updated

    async def run_now(
        self, context: AuthenticatedContext, competitor_id: str
    ) -> CompetitorScanResponse:
        await self.get(context, competitor_id)
        return await self._scan_service.start(
            context=context,
            competitor_id=competitor_id,
            idempotency_key=f"manual:{competitor_id}:{new_id()}",
            request=CompetitorScanRequest(scan_type=ScanType.RESCAN, rediscover_sources=False),
            trigger=ScanTrigger.MANUAL,
        )

    async def _require_competitor(self, organization_id: str, competitor_id: str) -> None:
        if await self._persistence.competitors.get_by_id(organization_id, competitor_id) is None:
            raise self._not_found()

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            code="MONITORING_SCHEDULE_NOT_FOUND",
            message="The monitoring schedule was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class DurableScheduler:
    def __init__(
        self,
        *,
        persistence: Persistence,
        scan_service: CompetitorScanService,
        settings: Settings,
        scheduler_id: str,
        clock: Clock | None = None,
        maintenance_queue: BackgroundJobQueue | None = None,
    ) -> None:
        self._persistence = persistence
        self._scan_service = scan_service
        self._settings = settings
        self.scheduler_id = scheduler_id
        self._clock = clock or SystemClock()
        self._maintenance_queue = maintenance_queue
        self._shutdown = False

    async def tick(self, *, limit: int = 100) -> int:
        processed = 0
        await self._enqueue_daily_maintenance()
        for _ in range(limit):
            now = self._clock.now()
            schedule = await self._persistence.monitoring_schedules.reserve_due(
                scheduler_id=self.scheduler_id,
                now=now,
                lease_duration=timedelta(seconds=self._settings.scheduler_lease_duration_seconds),
            )
            if schedule is None:
                break
            occurrence = schedule.next_run_at or now
            key = f"schedule:{schedule.id}:{occurrence.isoformat()}"
            next_run = next_run_for(
                schedule.cadence,
                after=occurrence,
                timezone_name=schedule.timezone,
                minimum_interval_hours=self._settings.monitoring_min_interval_hours,
            )
            job_id: str | None = None
            degraded: str | None = None
            try:
                response = await self._scan_service.start(
                    context=AuthenticatedContext(
                        user_id=schedule.created_by_user_id,
                        organization_id=schedule.organization_id,
                    ),
                    competitor_id=schedule.competitor_id,
                    idempotency_key=key,
                    request=CompetitorScanRequest(
                        scan_type=ScanType.RESCAN, rediscover_sources=False
                    ),
                    trigger=ScanTrigger.FUTURE_SCHEDULE,
                )
                job_id = response.job.id if response.job else response.scan.id
            except AppError as exc:
                if exc.code != "COMPETITOR_SCAN_ALREADY_RUNNING":
                    degraded = exc.code
            except JobExecutionFailure as exc:
                degraded = exc.code
                if exc.code in {
                    "ORGANIZATION_JOB_LIMIT_REACHED",
                    "GLOBAL_JOB_LIMIT_REACHED",
                    "ORGANIZATION_SCAN_LIMIT_REACHED",
                }:
                    next_run = now + timedelta(minutes=15)
                    logger.warning(
                        "monitoring_schedule_deferred",
                        organization_id=schedule.organization_id,
                        schedule_id=schedule.id,
                        competitor_id=schedule.competitor_id,
                        error_code=exc.code,
                    )
            await self._persistence.monitoring_schedules.release_reservation(
                schedule_id=schedule.id,
                scheduler_id=self.scheduler_id,
                now=now,
                next_run_at=next_run,
                job_id=job_id,
                degraded_reason=degraded,
            )
            processed += 1
        return processed

    async def _enqueue_daily_maintenance(self) -> None:
        if self._maintenance_queue is None:
            return
        now = self._clock.now()
        organization_ids = await self._persistence.company_profiles.list_organization_ids(
            limit=self._settings.job_recovery_batch_size
        )
        for organization_id in organization_ids:
            company = await self._persistence.company_profiles.get_for_organization(organization_id)
            if company is None:
                continue
            await self._maintenance_queue.enqueue(
                organization_id=organization_id,
                company_profile_id=company.id,
                job_type=JobType.RETENTION_CLEANUP,
                domain_resource_type="organization",
                domain_resource_id=organization_id,
                deduplication_key=f"retention:{now.date().isoformat()}",
                created_by_user_id=company.created_by_user_id,
                priority=-50,
                enforce_backpressure=False,
                deduplicate_terminal=True,
            )

    async def serve(self) -> None:
        while not self._shutdown:
            await self.tick()
            await asyncio.sleep(self._settings.scheduler_poll_interval_seconds)

    def shutdown(self) -> None:
        self._shutdown = True
