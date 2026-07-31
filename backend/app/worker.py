import asyncio
import signal
import socket
from collections.abc import Mapping
from contextlib import suppress
from uuid import uuid4

from app.core.clock import SystemClock
from app.core.config import get_settings
from app.domain.background_jobs import (
    BackgroundJob,
    JobType,
    ServiceInstance,
    ServiceType,
)
from app.main import create_app
from app.services.background_jobs import (
    BackgroundJobHandler,
    CleanupJobHandler,
    CompanyInitializationJobHandler,
    CompanyIntelligenceRebuildJobHandler,
    CompetitorScanJobHandler,
    DocumentIngestionJobHandler,
    DurableWorker,
    JobExecutionContext,
    RetentionCleanupJobHandler,
)
from app.services.company_documents import namespace_for_organization
from app.services.intelligence_jobs import (
    EvidenceCleanupJobHandler,
    EvidenceIndexingJobHandler,
    IntelligenceReindexJobHandler,
)
from app.services.strategy import StrategicAnalysisJobHandler


async def _run() -> None:
    settings = get_settings()
    if not settings.background_jobs_enabled:
        raise RuntimeError("BACKGROUND_JOBS_ENABLED must be true to start a worker")
    worker_id = f"{settings.worker_id_prefix}-{socket.gethostname()[:24]}-{uuid4().hex[:10]}"
    application = create_app(settings)
    clock = SystemClock()
    stop = asyncio.Event()

    async with application.router.lifespan_context(application):
        persistence = application.state.persistence
        storage = application.state.document_storage
        vectors = application.state.vector_store

        async def pinecone_cleanup(
            job: BackgroundJob, context: JobExecutionContext
        ) -> Mapping[str, object]:
            document = await persistence.company_documents.get_by_id(
                job.organization_id,
                job.domain_resource_id,
                include_deleted=True,
            )
            if document is None:
                return {"already_absent": True}
            if await context.cancelled(job):
                return {"cancelled": True}
            if job.payload.get("cleanup_scope") == "stale_version":
                raw_version = job.payload.get("document_version")
                version = (
                    int(raw_version)
                    if isinstance(raw_version, (str, int)) and not isinstance(raw_version, bool)
                    else 0
                )
                if (
                    version < 1
                    or document.active_version is None
                    or version >= document.active_version
                ):
                    return {"already_current": True}
                chunks = await persistence.company_knowledge_chunks.list_for_document_version(
                    job.organization_id,
                    document.id,
                    version,
                )
                if vectors.enabled:
                    await vectors.delete(
                        namespace_for_organization(
                            settings.pinecone_namespace_prefix,
                            job.organization_id,
                        ),
                        ids=[chunk.vector_id for chunk in chunks if chunk.vector_id],
                    )
                await persistence.company_knowledge_chunks.delete_version(
                    job.organization_id,
                    document.id,
                    version,
                )
                return {
                    "document_id": document.id,
                    "document_version": version,
                    "vectors_deleted": len([chunk for chunk in chunks if chunk.vector_id]),
                }
            if vectors.enabled:
                await vectors.delete(
                    namespace_for_organization(
                        settings.pinecone_namespace_prefix, job.organization_id
                    ),
                    metadata_filter={
                        "organization_id": {"$eq": job.organization_id},
                        "document_id": {"$eq": document.id},
                    },
                )
            await persistence.company_knowledge_chunks.delete_for_document(
                job.organization_id, document.id
            )
            return {"document_id": document.id}

        async def storage_cleanup(
            job: BackgroundJob, context: JobExecutionContext
        ) -> Mapping[str, object]:
            document = await persistence.company_documents.get_by_id(
                job.organization_id,
                job.domain_resource_id,
                include_deleted=True,
            )
            if document is None:
                return {"already_absent": True}
            if await context.cancelled(job):
                return {"cancelled": True}
            await storage.delete(document.storage_key)
            return {"document_id": document.id}

        handlers: dict[JobType, BackgroundJobHandler] = {
            JobType.COMPANY_INITIALIZATION: CompanyInitializationJobHandler(
                application.state.initialization_runner, persistence
            ),
            JobType.COMPANY_INTELLIGENCE_REBUILD: CompanyIntelligenceRebuildJobHandler(
                application.state.initialization_runner, persistence
            ),
            JobType.COMPETITOR_BASELINE_SCAN: CompetitorScanJobHandler(
                JobType.COMPETITOR_BASELINE_SCAN,
                application.state.competitor_scan_runner,
                persistence,
                application.state.evidence_indexing_scheduler,
            ),
            JobType.COMPETITOR_RESCAN: CompetitorScanJobHandler(
                JobType.COMPETITOR_RESCAN,
                application.state.competitor_scan_runner,
                persistence,
                application.state.evidence_indexing_scheduler,
            ),
            JobType.DOCUMENT_INGESTION: DocumentIngestionJobHandler(
                application.state.document_ingestion_runner, persistence
            ),
            JobType.PINECONE_CLEANUP: CleanupJobHandler(JobType.PINECONE_CLEANUP, pinecone_cleanup),
            JobType.DOCUMENT_STORAGE_CLEANUP: CleanupJobHandler(
                JobType.DOCUMENT_STORAGE_CLEANUP, storage_cleanup
            ),
            JobType.RETENTION_CLEANUP: RetentionCleanupJobHandler(
                persistence,
                application.state.background_job_queue,
                settings,
            ),
            JobType.COMPETITOR_SNAPSHOT_INDEXING: EvidenceIndexingJobHandler(
                JobType.COMPETITOR_SNAPSHOT_INDEXING,
                application.state.evidence_indexing_service,
            ),
            JobType.COMPETITOR_SIGNAL_INDEXING: EvidenceIndexingJobHandler(
                JobType.COMPETITOR_SIGNAL_INDEXING,
                application.state.evidence_indexing_service,
            ),
            JobType.COMPETITOR_INTELLIGENCE_REINDEX: IntelligenceReindexJobHandler(
                application.state.evidence_indexing_service
            ),
            JobType.COMPETITOR_EVIDENCE_CLEANUP: EvidenceCleanupJobHandler(
                application.state.evidence_indexing_service
            ),
            JobType.STRATEGIC_ANALYSIS: StrategicAnalysisJobHandler(
                application.state.strategic_analysis_runner
            ),
        }
        worker = DurableWorker(
            persistence=persistence,
            handlers=handlers,
            settings=settings,
            worker_id=worker_id,
            clock=clock,
        )

        async def service_heartbeat() -> None:
            started = clock.now()
            while not stop.is_set():
                now = clock.now()
                await persistence.service_instances.heartbeat(
                    ServiceInstance(
                        instance_id=worker_id,
                        service_type=ServiceType.WORKER,
                        started_at=started,
                        heartbeat_at=now,
                        version="0.1.0",
                        supported_job_types=list(handlers),
                    )
                )
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=max(1, settings.worker_heartbeat_ttl_seconds / 3),
                    )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        heartbeat_task = asyncio.create_task(service_heartbeat())
        worker_task = asyncio.create_task(worker.serve())
        await stop.wait()
        await worker.shutdown()
        worker_task.cancel()
        heartbeat_task.cancel()
        await asyncio.gather(worker_task, heartbeat_task, return_exceptions=True)


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_run())


if __name__ == "__main__":
    main()
