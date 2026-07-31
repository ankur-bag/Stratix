import asyncio
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.domain.background_jobs import (
    BackgroundJobStatus,
    JobType,
    Notification,
    NotificationSeverity,
    NotificationType,
    ServiceInstance,
    ServiceType,
)
from app.domain.documents import CompanyDocument, DocumentStage, DocumentStatus
from app.services.background_jobs import (
    BackgroundJobQueue,
    JobExecutionContext,
    RetentionCleanupJobHandler,
)
from tests.fakes import InMemoryPersistence


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def test_retention_cleanup_is_bounded_tenant_safe_and_idempotent() -> None:
    async def exercise() -> None:
        now = datetime(2026, 5, 1, tzinfo=UTC)
        clock = FakeClock(now)
        settings = Settings(
            app_env="testing",
            job_recovery_batch_size=100,
            job_cleanup_retention_days=30,
            dead_letter_retention_days=90,
            notification_retention_days=90,
            _env_file=None,
        )
        persistence = InMemoryPersistence()
        queue = BackgroundJobQueue(persistence, settings, clock)

        async def terminal(key: str, status: BackgroundJobStatus, age_days: int) -> None:
            job = await queue.enqueue(
                organization_id="org-a",
                company_profile_id="company-a",
                job_type=JobType.PINECONE_CLEANUP,
                domain_resource_type="document",
                domain_resource_id=key,
                deduplication_key=key,
            )
            persistence.background_jobs.items[job.id] = job.model_copy(
                update={
                    "status": status,
                    "completed_at": now - timedelta(days=age_days),
                    "updated_at": now - timedelta(days=age_days),
                }
            )

        await terminal("old-success", BackgroundJobStatus.SUCCEEDED, 31)
        await terminal("old-failure", BackgroundJobStatus.DEAD_LETTERED, 91)
        await terminal("recent-failure", BackgroundJobStatus.FAILED, 10)

        document = CompanyDocument(
            id="expired-document",
            organization_id="org-a",
            company_profile_id="company-a",
            uploaded_by_user_id="user-a",
            original_filename="expired.pdf",
            content_type="application/pdf",
            storage_key="org-a/expired-document.pdf",
            size_bytes=100,
            status=DocumentStatus.PENDING_UPLOAD,
            stage=DocumentStage.AWAITING_UPLOAD,
            progress_percent=0,
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=2),
        )
        await persistence.company_documents.create(document)
        await persistence.notifications.create(
            Notification(
                id="old-notification-a",
                organization_id="org-a",
                type=NotificationType.SCAN_FAILED,
                severity=NotificationSeverity.ERROR,
                title="Old",
                message="Old notification",
                resource_type="scan",
                resource_id="scan-a",
                deduplication_key="old-a",
                created_at=now - timedelta(days=91),
            )
        )
        await persistence.notifications.create(
            Notification(
                id="old-notification-b",
                organization_id="org-b",
                type=NotificationType.SCAN_FAILED,
                severity=NotificationSeverity.ERROR,
                title="Other tenant",
                message="Must remain",
                resource_type="scan",
                resource_id="scan-b",
                deduplication_key="old-b",
                created_at=now - timedelta(days=91),
            )
        )
        await persistence.service_instances.heartbeat(
            ServiceInstance(
                instance_id="old-worker",
                service_type=ServiceType.WORKER,
                started_at=now - timedelta(days=2),
                heartbeat_at=now - timedelta(days=2),
                version="test",
            )
        )

        cleanup = await queue.enqueue(
            organization_id="org-a",
            company_profile_id="company-a",
            job_type=JobType.RETENTION_CLEANUP,
            domain_resource_type="organization",
            domain_resource_id="org-a",
            deduplication_key="retention:2026-05-01",
            deduplicate_terminal=True,
        )
        claimed = await persistence.background_jobs.claim_next(
            worker_id="worker-a",
            supported_job_types={JobType.RETENTION_CLEANUP},
            now=now,
            lease_duration=timedelta(minutes=2),
        )
        assert claimed and claimed.id == cleanup.id
        running = await persistence.background_jobs.mark_running(
            job_id=cleanup.id, worker_id="worker-a", now=now
        )
        assert running
        result = await RetentionCleanupJobHandler(persistence, queue, settings).execute(
            job=running,
            context=JobExecutionContext(
                persistence=persistence,
                clock=clock,
                worker_id="worker-a",
                lease_lost=asyncio.Event(),
                shutdown_requested=asyncio.Event(),
            ),
        )

        assert result.result_summary["terminal_jobs_deleted"] == 2
        assert "recent-failure" in {
            item.deduplication_key for item in persistence.background_jobs.items.values()
        }
        assert "old-success" not in {
            item.deduplication_key for item in persistence.background_jobs.items.values()
        }
        assert persistence.company_documents.items[document.id].status == DocumentStatus.DELETED
        assert (
            sum(
                item.job_type == JobType.DOCUMENT_STORAGE_CLEANUP
                for item in persistence.background_jobs.items.values()
            )
            == 1
        )
        assert "old-notification-a" not in persistence.notifications.items
        assert "old-notification-b" in persistence.notifications.items
        assert "old-worker" not in persistence.service_instances.items

    asyncio.run(exercise())
