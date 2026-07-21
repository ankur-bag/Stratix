import asyncio
from typing import Protocol

from app.domain.onboarding import JobStage, OnboardingStatus
from app.repositories.interfaces import Persistence


class InitializationRunner(Protocol):
    """Replaceable deterministic company initialization worker."""

    async def run(self, *, job_id: str, organization_id: str) -> None: ...


class BackgroundExecutor(Protocol):
    """Replaceable boundary for scheduling persisted initialization jobs."""

    def schedule(self, *, job_id: str, organization_id: str) -> None: ...
    async def close(self) -> None: ...


class CompanyInitializationRunner:
    """Perform Phase 1 workspace preparation without external integrations."""

    def __init__(self, persistence: Persistence) -> None:
        self._persistence = persistence

    async def run(self, *, job_id: str, organization_id: str) -> None:
        job = await self._persistence.initialization_jobs.get_by_id(organization_id, job_id)
        if job is None:
            return
        try:
            await self._persistence.initialization_jobs.mark_running(organization_id, job_id)
            await self._persistence.initialization_jobs.update_progress(
                organization_id,
                job_id,
                JobStage.VALIDATING_COMPANY,
                15,
                "Validating the submitted company profile.",
            )
            company = await self._persistence.company_profiles.get_by_id(
                organization_id, job.company_profile_id
            )
            if company is None:
                raise InitializationInvariantError
            await self._persistence.company_profiles.update_onboarding_status(
                organization_id,
                company.id,
                OnboardingStatus.INITIALIZING,
            )
            required_lists = (
                company.target_customer_segments,
                company.primary_use_cases,
                company.strategic_goals,
            )
            if not all(required_lists):
                raise InitializationInvariantError
            await self._persistence.initialization_jobs.update_progress(
                organization_id,
                job_id,
                JobStage.CREATING_COMPANY_SOURCE,
                35,
                "Confirming the primary company website source.",
            )
            source = await self._persistence.sources.get_primary_for_company(
                organization_id, company.id
            )
            if source is None:
                raise InitializationInvariantError
            await self._persistence.sources.mark_ready(organization_id, source.id)
            await self._persistence.initialization_jobs.update_progress(
                organization_id,
                job_id,
                JobStage.PREPARING_INTELLIGENCE_WORKSPACE,
                65,
                "Preparing the intelligence workspace.",
            )
            await self._persistence.company_profiles.update_onboarding_status(
                organization_id,
                company.id,
                OnboardingStatus.INITIALIZING,
                workspace_metadata={"discovery_state": "prepared"},
            )
            await self._persistence.initialization_jobs.update_progress(
                organization_id,
                job_id,
                JobStage.READY_FOR_DISCOVERY,
                90,
                "The workspace is ready for future competitor discovery.",
            )
            await self._persistence.initialization_jobs.mark_succeeded(organization_id, job_id)
            await self._persistence.company_profiles.update_onboarding_status(
                organization_id,
                company.id,
                OnboardingStatus.READY,
                workspace_metadata={"discovery_state": "ready"},
            )
        except Exception:
            await self._persistence.initialization_jobs.mark_failed(
                organization_id,
                job_id,
                "INITIALIZATION_FAILED",
                "Company workspace initialization could not be completed.",
            )
            await self._persistence.company_profiles.update_onboarding_status(
                organization_id,
                job.company_profile_id,
                OnboardingStatus.FAILED,
            )


class InitializationInvariantError(Exception):
    """Persisted onboarding data does not satisfy initialization invariants."""


class InProcessBackgroundExecutor:
    """Schedule non-durable in-process jobs for local Phase 1 execution."""

    def __init__(self, runner: InitializationRunner) -> None:
        self._runner = runner
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, *, job_id: str, organization_id: str) -> None:
        task = asyncio.create_task(self._runner.run(job_id=job_id, organization_id=organization_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
