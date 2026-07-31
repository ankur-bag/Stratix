import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.core.config import Settings
from app.domain.background_jobs import MonitoringCadence, MonitoringSchedule
from app.services.background_jobs import JobExecutionFailure
from app.services.monitoring_schedules import DurableScheduler
from tests.fakes import InMemoryPersistence


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class FakeScanService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def start(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            job=SimpleNamespace(id=f"job-{len(self.calls)}"),
            scan=SimpleNamespace(id=f"scan-{len(self.calls)}"),
        )


def schedule(
    now: datetime,
    *,
    identifier: str,
    organization_id: str = "org-a",
    due: bool = True,
    enabled: bool = True,
) -> MonitoringSchedule:
    return MonitoringSchedule(
        id=identifier,
        organization_id=organization_id,
        company_profile_id=f"company-{organization_id}",
        competitor_id=f"competitor-{identifier}",
        cadence=MonitoringCadence.DAILY,
        is_enabled=enabled,
        next_run_at=now if due else now + timedelta(days=1),
        created_by_user_id="user",
        created_at=now,
        updated_at=now,
    )


def test_scheduler_ignores_future_and_disabled_and_enqueues_due_once() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        for item in (
            schedule(now, identifier="due"),
            schedule(now, identifier="future", due=False),
            schedule(now, identifier="disabled", enabled=False),
        ):
            await persistence.monitoring_schedules.create(item)
        scans = FakeScanService()
        scheduler = DurableScheduler(
            persistence=persistence,
            scan_service=scans,  # type: ignore[arg-type]
            settings=Settings(app_env="testing", _env_file=None),
            scheduler_id="scheduler-a",
            clock=FakeClock(now),
        )
        assert await scheduler.tick() == 1
        assert len(scans.calls) == 1
        assert scans.calls[0]["idempotency_key"] == (f"schedule:due:{now.isoformat()}")
        assert await scheduler.tick() == 0

    asyncio.run(exercise())


def test_two_schedulers_do_not_duplicate_an_occurrence_and_tenants_stay_separate() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await persistence.monitoring_schedules.create(
            schedule(now, identifier="a", organization_id="org-a")
        )
        await persistence.monitoring_schedules.create(
            schedule(now, identifier="b", organization_id="org-b")
        )
        scans = FakeScanService()
        settings = Settings(app_env="testing", _env_file=None)
        schedulers = [
            DurableScheduler(
                persistence=persistence,
                scan_service=scans,  # type: ignore[arg-type]
                settings=settings,
                scheduler_id=f"scheduler-{index}",
                clock=FakeClock(now),
            )
            for index in range(2)
        ]
        counts = await asyncio.gather(*(item.tick(limit=1) for item in schedulers))
        assert sum(counts) == 2
        assert {
            call["context"].organization_id
            for call in scans.calls  # type: ignore[union-attr]
        } == {"org-a", "org-b"}
        assert len({call["idempotency_key"] for call in scans.calls}) == 2

    asyncio.run(exercise())


def test_many_organizations_and_schedules_process_without_duplicate_occurrences() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        for organization in range(100):
            for competitor in range(10):
                await persistence.monitoring_schedules.create(
                    schedule(
                        now,
                        identifier=f"{organization}-{competitor}",
                        organization_id=f"org-{organization}",
                    )
                )
        scans = FakeScanService()
        scheduler = DurableScheduler(
            persistence=persistence,
            scan_service=scans,  # type: ignore[arg-type]
            settings=Settings(app_env="testing", _env_file=None),
            scheduler_id="scheduler-scale",
            clock=FakeClock(now),
        )
        assert await scheduler.tick(limit=1_000) == 1_000
        assert len({call["idempotency_key"] for call in scans.calls}) == 1_000

    asyncio.run(exercise())


def test_scheduler_defers_backpressured_occurrence_and_marks_it_degraded() -> None:
    async def exercise() -> None:
        class BackpressuredScanService(FakeScanService):
            async def start(self, **kwargs):
                raise JobExecutionFailure(
                    "GLOBAL_JOB_LIMIT_REACHED",
                    "The queue is full.",
                    retryable=True,
                )

        persistence = InMemoryPersistence()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        due = schedule(now, identifier="backpressured")
        await persistence.monitoring_schedules.create(due)
        scheduler = DurableScheduler(
            persistence=persistence,
            scan_service=BackpressuredScanService(),  # type: ignore[arg-type]
            settings=Settings(app_env="testing", _env_file=None),
            scheduler_id="scheduler-a",
            clock=FakeClock(now),
        )
        assert await scheduler.tick() == 1
        updated = persistence.monitoring_schedules.items[due.id]
        assert updated.next_run_at == now + timedelta(minutes=15)
        assert updated.degraded_reason == "GLOBAL_JOB_LIMIT_REACHED"
        assert updated.last_scheduled_at is None

    asyncio.run(exercise())
