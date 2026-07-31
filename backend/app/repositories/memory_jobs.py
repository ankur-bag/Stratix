import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from app.domain.background_jobs import (
    ACTIVE_JOB_STATUSES,
    BackgroundJob,
    BackgroundJobStatus,
    JobType,
    MonitoringSchedule,
    NewBackgroundJob,
    Notification,
    NotificationSeverity,
    NotificationType,
    ServiceInstance,
    ServiceType,
)
from app.domain.onboarding import new_id
from app.repositories.interfaces import RepositoryConflictError


class InMemoryBackgroundJobRepository:
    """Deterministic queue repository used by offline tests and diagnostics."""

    def __init__(self) -> None:
        self.items: dict[str, BackgroundJob] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, *, job: NewBackgroundJob) -> BackgroundJob:
        async with self._lock:
            existing = self._active(job.organization_id, job.deduplication_key)
            if existing is not None:
                raise RepositoryConflictError
            now = job.available_at
            record = BackgroundJob(id=new_id(), **job.model_dump(), created_at=now, updated_at=now)
            self.items[record.id] = record
            return record

    async def get_by_id(self, organization_id: str, job_id: str) -> BackgroundJob | None:
        item = self.items.get(job_id)
        return item if item and item.organization_id == organization_id else None

    async def get_active_by_deduplication_key(
        self, organization_id: str, deduplication_key: str
    ) -> BackgroundJob | None:
        return self._active(organization_id, deduplication_key)

    async def get_by_deduplication_key(
        self, organization_id: str, deduplication_key: str
    ) -> BackgroundJob | None:
        matches = [
            item
            for item in self.items.values()
            if item.organization_id == organization_id
            and item.deduplication_key == deduplication_key
        ]
        return max(matches, key=lambda item: item.created_at) if matches else None

    async def claim_next(
        self,
        *,
        worker_id: str,
        supported_job_types: set[JobType],
        now: datetime,
        lease_duration: timedelta,
        excluded_organization_id: str | None = None,
    ) -> BackgroundJob | None:
        async with self._lock:
            candidates = [
                item
                for item in self.items.values()
                if item.status in {BackgroundJobStatus.QUEUED, BackgroundJobStatus.RETRY_SCHEDULED}
                and item.available_at <= now
                and item.job_type in supported_job_types
                and (
                    excluded_organization_id is None
                    or item.organization_id != excluded_organization_id
                )
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: (-item.priority, item.available_at, item.created_at))
            item = candidates[0]
            updated = item.model_copy(
                update={
                    "status": BackgroundJobStatus.CLAIMED,
                    "worker_id": worker_id,
                    "last_worker_id": worker_id,
                    "claimed_at": now,
                    "heartbeat_at": now,
                    "lease_expires_at": now + lease_duration,
                    "attempt_count": item.attempt_count + 1,
                    "updated_at": now,
                }
            )
            self.items[item.id] = updated
            return updated

    async def renew_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool:
        item = self.items.get(job_id)
        if (
            item is None
            or item.worker_id != worker_id
            or item.status
            not in {
                BackgroundJobStatus.CLAIMED,
                BackgroundJobStatus.RUNNING,
                BackgroundJobStatus.CANCEL_REQUESTED,
            }
        ):
            return False
        self.items[job_id] = item.model_copy(
            update={
                "heartbeat_at": now,
                "lease_expires_at": now + lease_duration,
                "updated_at": now,
            }
        )
        return True

    async def mark_running(
        self, *, job_id: str, worker_id: str, now: datetime
    ) -> BackgroundJob | None:
        return self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.CLAIMED},
            {
                "status": BackgroundJobStatus.RUNNING,
                "started_at": now,
                "stage": "running",
                "message": "Background job is running.",
                "updated_at": now,
            },
        )

    async def update_progress(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        progress_percent: int,
        stage: str,
        message: str,
    ) -> BackgroundJob | None:
        return self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.RUNNING, BackgroundJobStatus.CANCEL_REQUESTED},
            {
                "progress_percent": progress_percent,
                "stage": stage,
                "message": message,
                "updated_at": now,
            },
        )

    async def mark_succeeded(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        status: BackgroundJobStatus,
        stage: str,
        message: str,
        result_summary: Mapping[str, Any],
    ) -> BackgroundJob | None:
        return self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.RUNNING},
            {
                "status": status,
                "progress_percent": 100,
                "stage": stage,
                "message": message,
                "result_summary": dict(result_summary),
                "completed_at": now,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            },
        )

    async def schedule_retry(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        available_at: datetime,
        error_code: str,
        error_message: str,
        error_class: str,
    ) -> BackgroundJob | None:
        return self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.RUNNING, BackgroundJobStatus.CLAIMED},
            {
                "status": BackgroundJobStatus.RETRY_SCHEDULED,
                "available_at": available_at,
                "stage": "retry_scheduled",
                "message": "The job will retry after a temporary failure.",
                "error_code": error_code,
                "error_message": error_message,
                "error_class": error_class,
                "retryable": True,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            },
        )

    async def mark_failed(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        error_code: str,
        error_message: str,
        error_class: str,
    ) -> BackgroundJob | None:
        return self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.RUNNING, BackgroundJobStatus.CLAIMED},
            {
                "status": BackgroundJobStatus.FAILED,
                "stage": "failed",
                "message": error_message,
                "error_code": error_code,
                "error_message": error_message,
                "error_class": error_class,
                "retryable": False,
                "completed_at": now,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            },
        )

    async def mark_dead_lettered(
        self,
        *,
        job_id: str,
        worker_id: str | None,
        now: datetime,
        error_code: str,
        error_message: str,
        error_class: str,
    ) -> BackgroundJob | None:
        item = self.items.get(job_id)
        if item is None or (worker_id is not None and item.worker_id != worker_id):
            return None
        updated = item.model_copy(
            update={
                "status": BackgroundJobStatus.DEAD_LETTERED,
                "stage": "dead_lettered",
                "message": error_message,
                "error_code": error_code,
                "error_message": error_message,
                "error_class": error_class,
                "retryable": False,
                "dead_lettered_at": now,
                "completed_at": now,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
        )
        self.items[job_id] = updated
        return updated

    async def request_cancellation(
        self, organization_id: str, job_id: str, now: datetime
    ) -> BackgroundJob | None:
        item = await self.get_by_id(organization_id, job_id)
        if item is None or item.status not in ACTIVE_JOB_STATUSES:
            return None
        immediate = item.status in {
            BackgroundJobStatus.QUEUED,
            BackgroundJobStatus.RETRY_SCHEDULED,
        }
        updated = item.model_copy(
            update={
                "status": (
                    BackgroundJobStatus.CANCELLED
                    if immediate
                    else BackgroundJobStatus.CANCEL_REQUESTED
                ),
                "cancelled_at": now if immediate else None,
                "completed_at": now if immediate else None,
                "updated_at": now,
            }
        )
        self.items[job_id] = updated
        return updated

    async def mark_cancelled(
        self, *, job_id: str, worker_id: str | None, now: datetime
    ) -> BackgroundJob | None:
        item = self.items.get(job_id)
        if (
            item is None
            or item.status != BackgroundJobStatus.CANCEL_REQUESTED
            or (worker_id is not None and item.worker_id != worker_id)
        ):
            return None
        updated = item.model_copy(
            update={
                "status": BackgroundJobStatus.CANCELLED,
                "stage": "cancelled",
                "message": "The job was cancelled at a safe checkpoint.",
                "cancelled_at": now,
                "completed_at": now,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
        )
        self.items[job_id] = updated
        return updated

    async def recover_expired_leases(self, *, now: datetime, limit: int) -> list[BackgroundJob]:
        expired = sorted(
            (
                item
                for item in self.items.values()
                if item.status
                in {
                    BackgroundJobStatus.CLAIMED,
                    BackgroundJobStatus.RUNNING,
                    BackgroundJobStatus.CANCEL_REQUESTED,
                }
                and item.lease_expires_at is not None
                and item.lease_expires_at < now
            ),
            key=lambda item: item.lease_expires_at or now,
        )[:limit]
        recovered: list[BackgroundJob] = []
        for item in expired:
            exhausted = item.attempt_count >= item.max_attempts
            target = (
                BackgroundJobStatus.DEAD_LETTERED
                if exhausted
                else BackgroundJobStatus.RETRY_SCHEDULED
            )
            updated = item.model_copy(
                update={
                    "status": target,
                    "stage": target.value,
                    "available_at": now,
                    "last_worker_id": item.worker_id,
                    "worker_id": None,
                    "lease_expires_at": None,
                    "dead_lettered_at": now if exhausted else None,
                    "completed_at": now if exhausted else None,
                    "error_code": "JOB_LEASE_EXPIRED",
                    "error_message": "The previous worker stopped reporting progress.",
                    "error_class": "LeaseExpired",
                    "retryable": not exhausted,
                    "updated_at": now,
                }
            )
            self.items[item.id] = updated
            recovered.append(updated)
        return recovered

    async def list(
        self,
        organization_id: str,
        *,
        status: BackgroundJobStatus | None,
        cursor: str | None,
        limit: int,
    ) -> list[BackgroundJob]:
        items = [
            item
            for item in self.items.values()
            if item.organization_id == organization_id
            and (status is None or item.status == status)
            and (cursor is None or item.id < cursor)
        ]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]

    async def count_active_for_organization(self, organization_id: str) -> int:
        return sum(
            item.organization_id == organization_id and item.status in ACTIVE_JOB_STATUSES
            for item in self.items.values()
        )

    async def count_queued(
        self, *, organization_id: str | None = None, scan_only: bool = False
    ) -> int:
        scan_types = {JobType.COMPETITOR_BASELINE_SCAN, JobType.COMPETITOR_RESCAN}
        return sum(
            item.status in {BackgroundJobStatus.QUEUED, BackgroundJobStatus.RETRY_SCHEDULED}
            and (organization_id is None or item.organization_id == organization_id)
            and (not scan_only or item.job_type in scan_types)
            for item in self.items.values()
        )

    async def operational_counts(self, now: datetime) -> tuple[int | None, int, int]:
        queued = [
            item.available_at
            for item in self.items.values()
            if item.status in {BackgroundJobStatus.QUEUED, BackgroundJobStatus.RETRY_SCHEDULED}
        ]
        oldest = max(0, int((now - min(queued)).total_seconds())) if queued else None
        expired = sum(
            item.status in {BackgroundJobStatus.CLAIMED, BackgroundJobStatus.RUNNING}
            and item.lease_expires_at is not None
            and item.lease_expires_at < now
            for item in self.items.values()
        )
        dead = sum(item.status == BackgroundJobStatus.DEAD_LETTERED for item in self.items.values())
        return oldest, expired, dead

    async def cleanup_terminal(
        self,
        organization_id: str,
        *,
        succeeded_before: datetime,
        failed_before: datetime,
        limit: int,
    ) -> int:
        removable = [
            item.id
            for item in sorted(
                self.items.values(), key=lambda item: item.completed_at or item.created_at
            )
            if item.organization_id == organization_id
            and item.completed_at is not None
            and (
                (
                    item.status
                    in {
                        BackgroundJobStatus.SUCCEEDED,
                        BackgroundJobStatus.PARTIALLY_SUCCEEDED,
                        BackgroundJobStatus.CANCELLED,
                    }
                    and item.completed_at < succeeded_before
                )
                or (
                    item.status
                    in {
                        BackgroundJobStatus.FAILED,
                        BackgroundJobStatus.DEAD_LETTERED,
                    }
                    and item.completed_at < failed_before
                )
            )
        ][:limit]
        for job_id in removable:
            self.items.pop(job_id, None)
        return len(removable)

    def _active(self, organization_id: str, key: str) -> BackgroundJob | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.organization_id == organization_id
                and item.deduplication_key == key
                and item.status in ACTIVE_JOB_STATUSES
            ),
            None,
        )

    def _owned_update(
        self,
        job_id: str,
        worker_id: str,
        statuses: set[BackgroundJobStatus],
        changes: Mapping[str, Any],
    ) -> BackgroundJob | None:
        item = self.items.get(job_id)
        if item is None or item.worker_id != worker_id or item.status not in statuses:
            return None
        updated = item.model_copy(update=dict(changes))
        self.items[job_id] = updated
        return updated


class InMemoryMonitoringScheduleRepository:
    def __init__(self) -> None:
        self.items: dict[str, MonitoringSchedule] = {}
        self._lock = asyncio.Lock()

    async def create(self, schedule: MonitoringSchedule) -> MonitoringSchedule:
        if await self.get_for_competitor(schedule.organization_id, schedule.competitor_id):
            raise RepositoryConflictError
        self.items[schedule.id] = schedule
        return schedule

    async def get_for_competitor(
        self, organization_id: str, competitor_id: str
    ) -> MonitoringSchedule | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.organization_id == organization_id and item.competitor_id == competitor_id
            ),
            None,
        )

    async def update(
        self, organization_id: str, schedule_id: str, changes: Mapping[str, Any]
    ) -> MonitoringSchedule | None:
        item = self.items.get(schedule_id)
        if item is None or item.organization_id != organization_id:
            return None
        updated = item.model_copy(update=dict(changes))
        self.items[schedule_id] = updated
        return updated

    async def reserve_due(
        self,
        *,
        scheduler_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> MonitoringSchedule | None:
        async with self._lock:
            due = sorted(
                (
                    item
                    for item in self.items.values()
                    if item.is_enabled
                    and item.cadence.value != "manual"
                    and item.next_run_at is not None
                    and item.next_run_at <= now
                    and (
                        item.scheduler_lease_expires_at is None
                        or item.scheduler_lease_expires_at < now
                    )
                ),
                key=lambda item: (item.next_run_at or now, item.id),
            )
            if not due:
                return None
            item = due[0]
            updated = item.model_copy(
                update={
                    "scheduler_lease_owner": scheduler_id,
                    "scheduler_lease_expires_at": now + lease_duration,
                    "updated_at": now,
                }
            )
            self.items[item.id] = updated
            return updated

    async def release_reservation(
        self,
        *,
        schedule_id: str,
        scheduler_id: str,
        now: datetime,
        next_run_at: datetime | None,
        job_id: str | None,
        degraded_reason: str | None = None,
    ) -> MonitoringSchedule | None:
        item = self.items.get(schedule_id)
        if item is None or item.scheduler_lease_owner != scheduler_id:
            return None
        updated = item.model_copy(
            update={
                "scheduler_lease_owner": None,
                "scheduler_lease_expires_at": None,
                "next_run_at": next_run_at,
                "last_scheduled_at": now if job_id else item.last_scheduled_at,
                "last_job_id": job_id or item.last_job_id,
                "degraded_reason": degraded_reason,
                "updated_at": now,
            }
        )
        self.items[item.id] = updated
        return updated


class InMemoryNotificationRepository:
    def __init__(self) -> None:
        self.items: dict[str, Notification] = {}

    async def create(self, notification: Notification) -> Notification:
        if any(
            item.organization_id == notification.organization_id
            and item.deduplication_key == notification.deduplication_key
            for item in self.items.values()
        ):
            raise RepositoryConflictError
        self.items[notification.id] = notification
        return notification

    async def list(
        self,
        organization_id: str,
        *,
        unread_only: bool,
        severity: NotificationSeverity | None,
        notification_type: NotificationType | None,
        cursor: str | None,
        limit: int,
    ) -> list[Notification]:
        items = [
            item
            for item in self.items.values()
            if item.organization_id == organization_id
            and (not unread_only or not item.is_read)
            and (severity is None or item.severity == severity)
            and (notification_type is None or item.type == notification_type)
            and (cursor is None or item.id < cursor)
        ]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]

    async def unread_count(self, organization_id: str) -> int:
        return sum(
            item.organization_id == organization_id and not item.is_read
            for item in self.items.values()
        )

    async def mark_read(
        self, organization_id: str, notification_id: str, now: datetime
    ) -> Notification | None:
        item = self.items.get(notification_id)
        if item is None or item.organization_id != organization_id:
            return None
        updated = item.model_copy(update={"is_read": True, "read_at": now})
        self.items[item.id] = updated
        return updated

    async def mark_all_read(self, organization_id: str, now: datetime) -> int:
        count = 0
        for item in list(self.items.values()):
            if item.organization_id == organization_id and not item.is_read:
                self.items[item.id] = item.model_copy(update={"is_read": True, "read_at": now})
                count += 1
        return count

    async def cleanup_expired(self, organization_id: str, *, before: datetime, limit: int) -> int:
        removable = [
            item.id
            for item in sorted(self.items.values(), key=lambda item: item.created_at)
            if item.organization_id == organization_id
            and (
                item.created_at < before
                or (item.expires_at is not None and item.expires_at < before)
            )
        ][:limit]
        for notification_id in removable:
            self.items.pop(notification_id, None)
        return len(removable)


class InMemoryServiceInstanceRepository:
    def __init__(self) -> None:
        self.items: dict[str, ServiceInstance] = {}

    async def heartbeat(self, instance: ServiceInstance) -> ServiceInstance:
        self.items[instance.instance_id] = instance
        return instance

    async def has_fresh(self, service_type: ServiceType, threshold: datetime) -> bool:
        return any(
            item.service_type == service_type and item.heartbeat_at >= threshold
            for item in self.items.values()
        )

    async def cleanup_expired(self, *, before: datetime, limit: int) -> int:
        removable = [
            item.instance_id
            for item in sorted(self.items.values(), key=lambda item: item.heartbeat_at)
            if item.heartbeat_at < before
        ][:limit]
        for instance_id in removable:
            self.items.pop(instance_id, None)
        return len(removable)
