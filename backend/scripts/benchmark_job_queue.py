import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.background_jobs import (
    BackgroundJobStatus,
    JobType,
    MonitoringCadence,
    MonitoringSchedule,
    NewBackgroundJob,
    Notification,
    NotificationSeverity,
    NotificationType,
)
from app.domain.onboarding import new_id
from app.repositories.memory_jobs import (
    InMemoryBackgroundJobRepository,
    InMemoryMonitoringScheduleRepository,
    InMemoryNotificationRepository,
)


async def main() -> None:
    jobs = InMemoryBackgroundJobRepository()
    schedules = InMemoryMonitoringScheduleRepository()
    notifications = InMemoryNotificationRepository()
    now = datetime.now(UTC)
    started = perf_counter()
    job_count = 10_000
    for index in range(job_count):
        organization_id = f"org-{index % 100}"
        await jobs.enqueue(
            job=NewBackgroundJob(
                organization_id=organization_id,
                job_type=JobType.COMPETITOR_RESCAN,
                domain_resource_type="competitor_scan",
                domain_resource_id=f"scan-{index}",
                deduplication_key=f"scan-{index}",
                payload={"competitor_id": f"competitor-{index % 1000}"},
                available_at=now,
            )
        )
    claims = 0
    for index in range(1_000):
        claimed = await jobs.claim_next(
            worker_id=f"worker-{index % 4}",
            supported_job_types={JobType.COMPETITOR_RESCAN},
            now=now,
            lease_duration=timedelta(seconds=120),
            excluded_organization_id=f"org-{(index - 1) % 100}" if index else None,
        )
        if claimed:
            claims += 1
    for item in list(jobs.items.values())[:100]:
        jobs.items[item.id] = item.model_copy(
            update={
                "status": BackgroundJobStatus.RUNNING,
                "attempt_count": item.max_attempts
                if int(item.domain_resource_id.split("-")[-1]) % 2
                else 1,
                "worker_id": "crashed-worker",
                "lease_expires_at": now - timedelta(seconds=1),
            }
        )
    recovered = await jobs.recover_expired_leases(now=now, limit=100)
    for organization_index in range(100):
        for competitor_index in range(10):
            schedule = MonitoringSchedule(
                id=new_id(),
                organization_id=f"org-{organization_index}",
                company_profile_id=f"company-{organization_index}",
                competitor_id=f"competitor-{organization_index}-{competitor_index}",
                cadence=MonitoringCadence.WEEKLY,
                next_run_at=now,
                created_by_user_id="benchmark",
                created_at=now,
                updated_at=now,
            )
            await schedules.create(schedule)
    schedules_processed = 0
    while True:
        reserved = await schedules.reserve_due(
            scheduler_id="benchmark-scheduler",
            now=now,
            lease_duration=timedelta(seconds=120),
        )
        if reserved is None:
            break
        await schedules.release_reservation(
            schedule_id=reserved.id,
            scheduler_id="benchmark-scheduler",
            now=now,
            next_run_at=now + timedelta(days=7),
            job_id=f"scheduled-job-{reserved.id}",
        )
        schedules_processed += 1
    for index in range(5_000):
        await notifications.create(
            Notification(
                id=f"{index:08d}",
                organization_id=f"org-{index % 100}",
                type=NotificationType.NEW_COMPETITOR_SIGNALS,
                severity=NotificationSeverity.INFO,
                title="New competitor signals",
                message="Synthetic benchmark notification.",
                resource_type="competitor",
                resource_id=f"competitor-{index}",
                deduplication_key=f"notification-{index}",
                created_at=now + timedelta(microseconds=index),
            )
        )
    elapsed = perf_counter() - started
    print(f"jobs_inserted={job_count}")
    print(f"claim_operations={claims}")
    print(f"recovery_operations={len(recovered)}")
    print(f"schedules_processed={schedules_processed}")
    print("notifications_generated=5000")
    print("queue_batch_size=1000")
    print("recovery_batch_size=100")
    print(f"duration_seconds={elapsed:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
