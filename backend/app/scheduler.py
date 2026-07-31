import asyncio
import signal
import socket
from contextlib import suppress
from uuid import uuid4

from app.core.clock import SystemClock
from app.core.config import get_settings
from app.domain.background_jobs import ServiceInstance, ServiceType
from app.main import create_app
from app.services.monitoring_schedules import DurableScheduler


async def _run() -> None:
    settings = get_settings()
    if not settings.scheduler_enabled:
        raise RuntimeError("SCHEDULER_ENABLED must be true to start the scheduler")
    scheduler_id = f"stratix-scheduler-{socket.gethostname()[:24]}-{uuid4().hex[:10]}"
    application = create_app(settings)
    clock = SystemClock()
    stop = asyncio.Event()
    async with application.router.lifespan_context(application):
        persistence = application.state.persistence
        scheduler = DurableScheduler(
            persistence=persistence,
            scan_service=application.state.competitor_scan_service,
            settings=settings,
            scheduler_id=scheduler_id,
            clock=clock,
            maintenance_queue=application.state.background_job_queue,
        )

        async def service_heartbeat() -> None:
            started = clock.now()
            while not stop.is_set():
                now = clock.now()
                await persistence.service_instances.heartbeat(
                    ServiceInstance(
                        instance_id=scheduler_id,
                        service_type=ServiceType.SCHEDULER,
                        started_at=started,
                        heartbeat_at=now,
                        version="0.1.0",
                    )
                )
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=max(1, settings.scheduler_heartbeat_ttl_seconds / 3),
                    )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        heartbeat_task = asyncio.create_task(service_heartbeat())
        scheduler_task = asyncio.create_task(scheduler.serve())
        await stop.wait()
        scheduler.shutdown()
        scheduler_task.cancel()
        heartbeat_task.cancel()
        await asyncio.gather(scheduler_task, heartbeat_task, return_exceptions=True)


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_run())


if __name__ == "__main__":
    main()
