import asyncio
from datetime import UTC, datetime, timedelta

from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.domain.background_jobs import MonitoringCadence, MonitoringScheduleUpdate
from app.domain.discovery import (
    Competitor,
    CompetitorStatus,
    RelationshipType,
)
from app.services.monitoring_schedules import (
    MonitoringScheduleService,
    next_run_for,
)
from app.services.scanning import CompetitorScanService
from tests.fakes import InMemoryPersistence, RecordingCompetitorScanExecutor


def test_next_run_calculation_for_supported_cadences_and_minimum_interval() -> None:
    now = datetime(2026, 3, 8, 9, tzinfo=UTC)
    assert (
        next_run_for(
            MonitoringCadence.MANUAL,
            after=now,
            timezone_name="UTC",
            minimum_interval_hours=24,
        )
        is None
    )
    assert next_run_for(
        MonitoringCadence.DAILY,
        after=now,
        timezone_name="UTC",
        minimum_interval_hours=24,
    ) == now + timedelta(days=1)
    assert next_run_for(
        MonitoringCadence.EVERY_3_DAYS,
        after=now,
        timezone_name="UTC",
        minimum_interval_hours=24,
    ) == now + timedelta(days=3)
    assert next_run_for(
        MonitoringCadence.WEEKLY,
        after=now,
        timezone_name="UTC",
        minimum_interval_hours=24,
    ) == now + timedelta(days=7)


def test_schedule_enable_pause_cadence_and_tenant_isolation() -> None:
    async def exercise() -> None:
        persistence = InMemoryPersistence()
        settings = Settings(app_env="testing", background_jobs_enabled=False, _env_file=None)
        scans = CompetitorScanService(persistence, RecordingCompetitorScanExecutor())
        service = MonitoringScheduleService(persistence, scans, settings)
        now = datetime.now(UTC)
        for organization_id, competitor_id in (
            ("org-a", "competitor-a"),
            ("org-b", "competitor-b"),
        ):
            await persistence.competitors.create(
                Competitor(
                    id=competitor_id,
                    organization_id=organization_id,
                    company_profile_id=f"company-{organization_id}",
                    candidate_id=f"candidate-{organization_id}",
                    name=competitor_id,
                    canonical_url=f"https://{competitor_id}.example.com",
                    canonical_domain=f"{competitor_id}.example.com",
                    description="Test competitor",
                    relationship_type=RelationshipType.DIRECT,
                    status=CompetitorStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                    approved_at=now,
                    approved_by_user_id="user",
                )
            )
        context = AuthenticatedContext(user_id="user", organization_id="org-a")
        created = await service.ensure_default(
            organization_id="org-a",
            company_profile_id="company-org-a",
            competitor_id="competitor-a",
            created_by_user_id="user",
        )
        replay = await service.ensure_default(
            organization_id="org-a",
            company_profile_id="company-org-a",
            competitor_id="competitor-a",
            created_by_user_id="user",
        )
        assert replay.id == created.id
        assert created.cadence == MonitoringCadence.WEEKLY
        assert (
            await persistence.monitoring_schedules.get_for_competitor("org-b", "competitor-a")
            is None
        )
        paused = await service.update(
            context,
            "competitor-a",
            request=MonitoringScheduleUpdate(is_enabled=False, cadence=MonitoringCadence.MANUAL),
        )
        assert not paused.is_enabled
        assert paused.next_run_at is None

    asyncio.run(exercise())
