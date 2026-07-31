import asyncio
from datetime import UTC, datetime, timedelta

from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.domain.background_jobs import (
    BackgroundJobStatus,
    JobType,
    Notification,
    NotificationSeverity,
    NotificationType,
)
from app.services.background_jobs import BackgroundJobQueue, NotificationEmitter
from app.services.notifications import NotificationService
from tests.fakes import InMemoryPersistence


def notification(
    identifier: str,
    *,
    organization_id: str = "org-a",
    created_at: datetime,
    unread: bool = True,
) -> Notification:
    return Notification(
        id=identifier,
        organization_id=organization_id,
        type=NotificationType.NEW_COMPETITOR_SIGNALS,
        severity=NotificationSeverity.INFO,
        title="New changes",
        message="Evidence-backed changes are ready.",
        resource_type="competitor",
        resource_id="competitor-a",
        action_url="/competitors/competitor-a",
        is_read=not unread,
        deduplication_key=f"notification-{identifier}",
        created_at=created_at,
    )


def test_notification_list_count_read_one_read_all_and_tenant_isolation() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        for index in range(30):
            await persistence.notifications.create(
                notification(
                    f"{index:03d}",
                    created_at=now + timedelta(seconds=index),
                )
            )
        foreign = notification(
            "foreign", organization_id="org-b", created_at=now + timedelta(days=1)
        )
        await persistence.notifications.create(foreign)
        service = NotificationService(persistence)
        context = AuthenticatedContext(user_id="user", organization_id="org-a")
        page = await service.list(
            context,
            unread_only=True,
            severity=None,
            notification_type=None,
            cursor=None,
            limit=10,
        )
        assert len(page.items) == 10
        assert page.next_cursor is not None
        assert all(item.organization_id == "org-a" for item in page.items)
        assert (await service.unread_count(context)).unread_count == 30
        await service.mark_read(context, page.items[0].id)
        assert (await service.unread_count(context)).unread_count == 29
        assert (await service.mark_all_read(context)).unread_count == 0
        assert await persistence.notifications.unread_count("org-b") == 1

    asyncio.run(exercise())


def test_notification_repository_handles_thousands_with_bounded_pages() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        for index in range(3_000):
            await persistence.notifications.create(
                notification(
                    f"{index:05d}",
                    organization_id=f"org-{index % 10}",
                    created_at=now + timedelta(microseconds=index),
                )
            )
        items = await persistence.notifications.list(
            "org-0",
            unread_only=False,
            severity=None,
            notification_type=None,
            cursor=None,
            limit=25,
        )
        assert len(items) == 25
        assert all(item.organization_id == "org-0" for item in items)

    asyncio.run(exercise())


def test_job_notification_rules_suppress_noise_and_deduplicate_final_events() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        settings = Settings(app_env="testing", _env_file=None)
        queue = BackgroundJobQueue(persistence, settings)
        emitter = NotificationEmitter(persistence, settings)

        changed = await queue.enqueue(
            organization_id="org-a",
            company_profile_id="company-a",
            job_type=JobType.COMPETITOR_RESCAN,
            domain_resource_type="competitor_scan",
            domain_resource_id="scan-a",
            deduplication_key="scan-a",
            payload={"competitor_id": "competitor-a"},
        )
        changed = changed.model_copy(
            update={
                "status": BackgroundJobStatus.SUCCEEDED,
                "result_summary": {
                    "signal_count": 2,
                    "competitor_id": "competitor-a",
                },
            }
        )
        await emitter.for_job_result(changed)
        await emitter.for_job_result(changed)

        unchanged = changed.model_copy(
            update={"id": "unchanged", "result_summary": {"signal_count": 0}}
        )
        await emitter.for_job_result(unchanged)
        failed = changed.model_copy(
            update={
                "id": "failed",
                "status": BackgroundJobStatus.DEAD_LETTERED,
                "error_message": "The scan exhausted its retries.",
            }
        )
        await emitter.for_job_result(failed, final_failure=True)

        items = await persistence.notifications.list(
            "org-a",
            unread_only=False,
            severity=None,
            notification_type=None,
            cursor=None,
            limit=10,
        )
        assert len(items) == 2
        assert {item.type for item in items} == {
            NotificationType.NEW_COMPETITOR_SIGNALS,
            NotificationType.SCAN_FAILED,
        }

    asyncio.run(exercise())
