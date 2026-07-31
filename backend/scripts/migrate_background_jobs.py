import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.domain.background_jobs import JobType
from app.repositories.mongo import MongoPersistence
from app.services.background_jobs import BackgroundJobQueue


async def run(command: str, confirmed: bool) -> None:
    settings = get_settings()
    persistence = MongoPersistence(settings)
    await persistence.start()
    try:
        database = persistence._database
        if database is None:
            raise RuntimeError("MongoDB is unavailable")
        collections = (
            ("initialization_jobs", "initialization_job", JobType.COMPANY_INITIALIZATION),
            ("document_ingestion_jobs", "document_ingestion", JobType.DOCUMENT_INGESTION),
            ("competitor_scan_jobs", "competitor_scan", None),
        )
        planned: list[tuple[str, str, JobType, dict[str, object]]] = []
        for collection_name, resource_type, fixed_type in collections:
            documents = (
                await database[collection_name]
                .find(
                    {"status": {"$in": ["queued", "running"]}},
                    {
                        "_id": 1,
                        "organization_id": 1,
                        "company_profile_id": 1,
                        "competitor_id": 1,
                        "scan_type": 1,
                        "requested_by_user_id": 1,
                    },
                )
                .to_list(length=100_000)
            )
            for document in documents:
                job_type = fixed_type or (
                    JobType.COMPETITOR_BASELINE_SCAN
                    if document.get("scan_type") == "baseline"
                    else JobType.COMPETITOR_RESCAN
                )
                planned.append(
                    (
                        collection_name,
                        resource_type,
                        job_type,
                        document,
                    )
                )
        if command in {"plan", "status"}:
            print(f"legacy_active_records={len(planned)}")
            print(f"mode={command}")
            return
        if not confirmed:
            raise RuntimeError("Apply requires --confirm-apply")
        queue = BackgroundJobQueue(persistence, settings)
        created = 0
        for _, resource_type, job_type, document in planned:
            resource_id = str(document["_id"])
            organization_id = str(document["organization_id"])
            payload = (
                {"competitor_id": str(document.get("competitor_id", ""))}
                if resource_type == "competitor_scan"
                else {}
            )
            existing = await persistence.background_jobs.get_active_by_deduplication_key(
                organization_id, f"{job_type.value}:{resource_id}"
            )
            if existing:
                continue
            await queue.enqueue(
                organization_id=organization_id,
                company_profile_id=(
                    str(document["company_profile_id"])
                    if document.get("company_profile_id")
                    else None
                ),
                job_type=job_type,
                domain_resource_type=resource_type,
                domain_resource_id=resource_id,
                deduplication_key=f"{job_type.value}:{resource_id}",
                payload=payload,
                created_by_user_id=(
                    str(document["requested_by_user_id"])
                    if document.get("requested_by_user_id")
                    else None
                ),
                enforce_backpressure=False,
            )
            created += 1
        print(f"background_jobs_created={created}")
    finally:
        await persistence.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan or apply durable-job migration.")
    parser.add_argument("command", choices=("plan", "apply", "status"))
    parser.add_argument("--confirm-apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.command, args.confirm_apply))


if __name__ == "__main__":
    main()
