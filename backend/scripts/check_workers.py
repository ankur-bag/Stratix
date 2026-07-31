import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.clock import SystemClock
from app.core.config import get_settings
from app.domain.background_jobs import ServiceType
from app.repositories.mongo import MongoPersistence


async def inspect(command: str) -> None:
    settings = get_settings()
    persistence = MongoPersistence(settings)
    await persistence.start()
    try:
        clock = SystemClock()
        now = clock.now()
        oldest, expired, dead = await persistence.background_jobs.operational_counts(now)
        worker = await persistence.service_instances.has_fresh(
            ServiceType.WORKER,
            now - timedelta(seconds=settings.worker_heartbeat_ttl_seconds),
        )
        scheduler = await persistence.service_instances.has_fresh(
            ServiceType.SCHEDULER,
            now - timedelta(seconds=settings.scheduler_heartbeat_ttl_seconds),
        )
        if command == "status":
            print(f"worker_available={str(worker).lower()}")
            print(f"scheduler_available={str(scheduler).lower()}")
        elif command == "queue":
            print(f"oldest_queued_job_age_seconds={oldest}")
            print(f"queued_jobs={await persistence.background_jobs.count_queued()}")
        elif command == "leases":
            print(f"expired_leases={expired}")
        elif command == "dead-letters":
            print(f"dead_letter_count={dead}")
    finally:
        await persistence.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Stratix worker diagnostics.")
    parser.add_argument("command", choices=("status", "queue", "leases", "dead-letters"))
    args = parser.parse_args()
    asyncio.run(inspect(args.command))


if __name__ == "__main__":
    main()
