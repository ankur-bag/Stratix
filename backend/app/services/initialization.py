import asyncio
from typing import Protocol

from app.domain.onboarding import OnboardingStatus
from app.graphs.company_initialization import CompanyInitializationGraph
from app.repositories.interfaces import Persistence


class InitializationRunner(Protocol):
    """Replaceable deterministic company initialization worker."""

    async def run(self, *, job_id: str, organization_id: str) -> None: ...


class BackgroundExecutor(Protocol):
    """Replaceable boundary for scheduling persisted initialization jobs."""

    def schedule(self, *, job_id: str, organization_id: str) -> None: ...
    async def close(self) -> None: ...


class CompanyInitializationRunner:
    """Execute the typed Phase 2 company intelligence graph."""

    def __init__(self, persistence: Persistence, graph: CompanyInitializationGraph) -> None:
        self._persistence = persistence
        self._graph = graph

    async def run(self, *, job_id: str, organization_id: str) -> None:
        job = await self._persistence.initialization_jobs.get_by_id(organization_id, job_id)
        if job is None:
            return
        try:
            await self._graph.run(
                job_id=job_id,
                organization_id=organization_id,
                company_profile_id=job.company_profile_id,
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


class InProcessBackgroundExecutor:
    """Schedule non-durable in-process jobs for local Phase 2 execution."""

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
