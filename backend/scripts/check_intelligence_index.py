import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.domain.background_jobs import BackgroundJobStatus, JobType
from app.repositories.mongo import MongoPersistence


async def run(command: str, organization_id: str | None) -> None:
    settings = get_settings()
    persistence = MongoPersistence(settings)
    await persistence.start()
    try:
        if not await persistence.ping():
            raise SystemExit("MongoDB is unavailable.")
        print(f"indexing_enabled={str(settings.competitor_evidence_indexing_enabled).lower()}")
        print(f"indexing_required={str(settings.competitor_evidence_indexing_required).lower()}")
        print(f"embedding_dimensions={settings.embedding_dimensions}")
        print(f"pinecone_dimensions={settings.pinecone_dimensions}")
        print(f"processing_namespace_prefix={settings.pinecone_namespace_prefix}")
        if not organization_id:
            print("organization_scope=not_requested")
            return
        active, inactive, latest = await persistence.intelligence_evidence.count_status(
            organization_id
        )
        evidence = await persistence.intelligence_evidence.list_active(
            organization_id,
            limit=1000,
        )
        indexing_job_types = {
            JobType.COMPETITOR_SNAPSHOT_INDEXING,
            JobType.COMPETITOR_SIGNAL_INDEXING,
            JobType.COMPETITOR_INTELLIGENCE_REINDEX,
            JobType.COMPETITOR_EVIDENCE_CLEANUP,
        }
        pending_jobs = []
        for status in (
            BackgroundJobStatus.QUEUED,
            BackgroundJobStatus.CLAIMED,
            BackgroundJobStatus.RUNNING,
            BackgroundJobStatus.RETRY_SCHEDULED,
        ):
            pending_jobs.extend(
                await persistence.background_jobs.list(
                    organization_id,
                    status=status,
                    cursor=None,
                    limit=1000,
                )
            )
        failed_jobs = []
        for status in (
            BackgroundJobStatus.FAILED,
            BackgroundJobStatus.DEAD_LETTERED,
        ):
            failed_jobs.extend(
                await persistence.background_jobs.list(
                    organization_id,
                    status=status,
                    cursor=None,
                    limit=1000,
                )
            )
        pending_jobs = [job for job in pending_jobs if job.job_type in indexing_job_types]
        failed_jobs = [job for job in failed_jobs if job.job_type in indexing_job_types]
        print(f"organization_id={organization_id}")
        print(f"active_evidence={active}")
        print(f"indexed_evidence={sum(bool(item.vector_id) for item in evidence)}")
        print(f"missing_vector_ids={sum(not item.vector_id for item in evidence)}")
        print(f"inactive_evidence={inactive}")
        print(f"stale_or_inactive_evidence={inactive}")
        print(f"pending_jobs={len(pending_jobs)}")
        print(f"failed_jobs={len(failed_jobs)}")
        print(f"last_indexed_at={latest.isoformat() if latest else 'none'}")
        if command in {"consistency", "sample"}:
            evidence = evidence[: 10 if command == "sample" else 100]
            missing_competitors = 0
            for item in evidence:
                if item.competitor_id and (
                    await persistence.competitors.get_by_id(
                        organization_id,
                        item.competitor_id,
                    )
                    is None
                ):
                    missing_competitors += 1
            invalid = [
                item.id
                for item in evidence
                if item.organization_id != organization_id
                or not item.active
                or item.deleted
                or item.embedding_dimensions != 768
                or not item.vector_id
                or not item.version_key.startswith(f"{item.source_type.value}:")
            ]
            print(f"checked_records={len(evidence)}")
            print(f"invalid_records={len(invalid)}")
            print(f"deleted_competitor_references={missing_competitors}")
            if command == "sample":
                for item in evidence:
                    print(
                        "sample="
                        f"{item.id},{item.source_type.value},{item.category.value},"
                        f"{item.observed_at.isoformat()}"
                    )
            if invalid or missing_competitors:
                raise SystemExit("Canonical evidence consistency checks failed.")
    finally:
        await persistence.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only canonical intelligence evidence diagnostics."
    )
    parser.add_argument("command", choices=["status", "consistency", "sample"])
    parser.add_argument(
        "--organization-id",
        help="Organization scope required for canonical evidence counts and samples.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.command, args.organization_id))


if __name__ == "__main__":
    main()
