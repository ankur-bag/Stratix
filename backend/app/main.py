from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.health import router as health_router
from app.api.v1.router import router as api_v1_router
from app.core.auth import (
    AuthenticationVerifier,
    ClerkAuthenticationVerifier,
    DevelopmentAuthenticationVerifier,
    UnavailableAuthenticationVerifier,
)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.crawlers.crawl4ai_adapter import Crawl4AIWebsiteCrawler
from app.crawlers.models import WebsiteCrawler
from app.crawlers.policy import CrawlPolicy, SystemDNSResolver
from app.domain.background_jobs import JobType
from app.graphs.company_initialization import CompanyInitializationGraph
from app.graphs.competitor_scan import CompetitorScanGraph
from app.middleware import RequestContextMiddleware
from app.repositories.interfaces import Persistence
from app.repositories.mongo import MongoPersistence
from app.services.background_jobs import (
    BackgroundJobQueue,
    BackgroundJobService,
    DurableCompetitorScanExecutor,
    DurableJobExecutor,
)
from app.services.change_detection import DeterministicChangeDetector
from app.services.company_documents import (
    CompanyDocumentService,
    DocumentIngestionRunner,
)
from app.services.competitor_sources import (
    CompetitorSourceService,
    DeterministicCompetitorSourceDiscoveryService,
)
from app.services.discovery import (
    CandidateClassifier,
    CompetitorDiscoveryRunner,
    CompetitorDiscoveryService,
)
from app.services.discovery_provider import (
    CompetitorSearchProvider,
    TavilyCompetitorSearchProvider,
    UnavailableCompetitorSearchProvider,
)
from app.services.document_storage import (
    FakeObjectStorage,
    LocalObjectStorage,
    ObjectStorage,
    S3ObjectStorage,
)
from app.services.embeddings import (
    DisabledEmbeddingProvider,
    EmbeddingProvider,
    GeminiEmbeddingProvider,
)
from app.services.extraction import (
    CompanyIntelligenceExtractor,
    DeterministicCompanyIntelligenceExtractor,
    UnavailableCompanyIntelligenceExtractor,
)
from app.services.initialization import (
    BackgroundExecutor,
    CompanyInitializationRunner,
    InProcessBackgroundExecutor,
)
from app.services.intelligence import (
    CompetitorEvidenceIndexingService,
    IntelligenceSearchService,
)
from app.services.intelligence_jobs import (
    EvidenceIndexingScheduler,
    IntelligenceReindexService,
)
from app.services.monitoring_schedules import MonitoringScheduleService
from app.services.normalization import ContentNormalizer
from app.services.notifications import NotificationService
from app.services.onboarding import CompanyOnboardingService
from app.services.pdf_processing import DeterministicPdfChunker, PdfExtractor
from app.services.scanning import (
    CompetitorScanExecutor,
    CompetitorScanService,
    GraphCompetitorScanRunner,
    InProcessCompetitorScanExecutor,
)
from app.services.signals import CompetitorSignalService, DeterministicSignalBuilder
from app.services.vector_store import (
    DisabledVectorStore,
    FakeVectorStore,
    PineconeVectorStore,
    VectorStore,
    VectorStoreError,
)


def create_app(
    settings: Settings | None = None,
    *,
    persistence: Persistence | None = None,
    background_executor: BackgroundExecutor | None = None,
    crawler: WebsiteCrawler | None = None,
    intelligence_extractor: CompanyIntelligenceExtractor | None = None,
    authentication_verifier: AuthenticationVerifier | None = None,
    discovery_background_executor: BackgroundExecutor | None = None,
    search_provider: CompetitorSearchProvider | None = None,
    candidate_classifier: CandidateClassifier | None = None,
    competitor_scan_executor: CompetitorScanExecutor | None = None,
    document_storage: ObjectStorage | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    document_background_executor: BackgroundExecutor | None = None,
) -> FastAPI:
    """Create and configure a Stratix API application instance."""
    application_settings = settings or get_settings()
    configure_logging(application_settings.log_level)
    logger = get_logger(__name__)
    application_persistence = persistence or cast(
        Persistence, MongoPersistence(application_settings)
    )
    application_crawl_policy = CrawlPolicy(SystemDNSResolver())
    application_crawler = crawler or Crawl4AIWebsiteCrawler(
        application_settings, application_crawl_policy
    )
    application_extractor = intelligence_extractor or (
        UnavailableCompanyIntelligenceExtractor()
        if application_settings.llm_enabled
        else DeterministicCompanyIntelligenceExtractor()
    )
    if authentication_verifier is not None:
        application_authentication_verifier = authentication_verifier
    elif application_settings.app_env == "development" and application_settings.auth_disabled:
        application_authentication_verifier = DevelopmentAuthenticationVerifier(
            user_id=application_settings.dev_user_id,
            organization_id=application_settings.dev_organization_id,
        )
    elif application_settings.clerk_verification_configured:
        application_authentication_verifier = ClerkAuthenticationVerifier(
            secret_key=application_settings.clerk_secret_key,
            jwt_key=application_settings.clerk_jwt_key,
            authorized_parties=application_settings.clerk_authorized_parties,
            require_organization=application_settings.clerk_require_organization,
            clock_skew_ms=application_settings.clerk_clock_skew_ms,
        )
    else:
        application_authentication_verifier = UnavailableAuthenticationVerifier()
    application_search_provider = search_provider or (
        TavilyCompetitorSearchProvider(application_settings.tavily_api_key)
        if application_settings.tavily_api_key
        else UnavailableCompetitorSearchProvider()
    )
    if document_storage is not None:
        application_document_storage = document_storage
    elif (
        application_settings.document_storage_provider == "fake"
        or application_settings.app_env == "testing"
    ):
        application_document_storage = FakeObjectStorage()
    elif application_settings.document_storage_provider == "s3":
        application_document_storage = S3ObjectStorage(
            bucket=application_settings.document_s3_bucket or "",
            region=application_settings.document_s3_region,
            endpoint_url=application_settings.document_s3_endpoint_url,
            upload_ttl_seconds=application_settings.document_upload_url_ttl_seconds,
        )
    else:
        application_document_storage = LocalObjectStorage(
            application_settings.document_storage_path
        )
    application_embeddings = embedding_provider or (
        GeminiEmbeddingProvider(
            api_key=application_settings.gemini_api_key or "",
            model=application_settings.embedding_model,
            dimensions=application_settings.embedding_dimensions,
            document_task_type=application_settings.embedding_document_task_type,
            query_task_type=application_settings.embedding_query_task_type,
            batch_size=application_settings.embedding_batch_size,
            maximum_concurrency=application_settings.embedding_max_concurrency,
            maximum_retries=application_settings.embedding_max_retries,
            retry_base_seconds=application_settings.embedding_retry_base_seconds,
            version=application_settings.embedding_version,
        )
        if application_settings.embeddings_enabled
        else DisabledEmbeddingProvider(
            application_settings.embedding_dimensions,
            application_settings.embedding_version,
        )
    )
    if vector_store is not None:
        application_vectors = vector_store
    elif not application_settings.vector_search_enabled:
        application_vectors = DisabledVectorStore()
    elif application_settings.vector_db_provider == "fake":
        application_vectors = FakeVectorStore(application_settings.pinecone_dimensions)
    elif application_settings.vector_db_provider == "disabled":
        application_vectors = DisabledVectorStore()
    else:
        application_vectors = PineconeVectorStore(
            api_key=application_settings.pinecone_api_key or "",
            host=application_settings.pinecone_host or "",
            index_name=application_settings.pinecone_index_name,
            dimensions=application_settings.pinecone_dimensions,
            metric=application_settings.pinecone_metric,
            upsert_batch_size=application_settings.pinecone_upsert_batch_size,
            maximum_concurrency=application_settings.pinecone_max_concurrency,
            maximum_retries=application_settings.pinecone_max_retries,
            retry_base_seconds=application_settings.pinecone_retry_base_seconds,
        )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await application_persistence.start()
        if hasattr(application_document_storage, "start"):
            await application_document_storage.start()
        active_vectors = application_vectors
        try:
            await active_vectors.start()
        except VectorStoreError:
            if application_settings.vector_search_required:
                await active_vectors.close()
                raise
            logger.warning("vector_search_degraded")
            await active_vectors.close()
            active_vectors = DisabledVectorStore()
            await active_vectors.start()
        job_queue = BackgroundJobQueue(application_persistence, application_settings)
        evidence_indexing_service = CompetitorEvidenceIndexingService(
            persistence=application_persistence,
            embeddings=application_embeddings,
            vectors=active_vectors,
            settings=application_settings,
        )
        evidence_indexing_scheduler = EvidenceIndexingScheduler(application_persistence, job_queue)
        document_runner = DocumentIngestionRunner(
            persistence=application_persistence,
            storage=application_document_storage,
            extractor=PdfExtractor(
                maximum_pages=application_settings.document_max_pages,
                maximum_characters=application_settings.document_max_extracted_characters,
            ),
            chunker=DeterministicPdfChunker(
                target_characters=application_settings.document_chunk_target_characters,
                maximum_characters=application_settings.document_chunk_max_characters,
                overlap_characters=application_settings.document_chunk_overlap_characters,
                minimum_characters=application_settings.document_chunk_min_characters,
            ),
            embeddings=application_embeddings,
            vectors=active_vectors,
            settings=application_settings,
            cleanup_queue=job_queue,
        )
        if document_background_executor is not None:
            document_executor = document_background_executor
        elif application_settings.background_jobs_enabled:
            document_executor = DurableJobExecutor(
                job_queue,
                application_persistence,
                JobType.DOCUMENT_INGESTION,
                "document_ingestion",
            )
        else:
            document_executor = InProcessBackgroundExecutor(document_runner)
        document_service = CompanyDocumentService(
            persistence=application_persistence,
            storage=application_document_storage,
            executor=document_executor,
            embeddings=application_embeddings,
            vectors=active_vectors,
            settings=application_settings,
            cleanup_queue=job_queue,
        )
        source_service = CompetitorSourceService(application_persistence)
        source_discovery = DeterministicCompetitorSourceDiscoveryService(
            application_persistence,
            application_crawler,
            application_crawl_policy,
            application_settings,
        )
        scan_graph = CompetitorScanGraph(
            persistence=application_persistence,
            crawler=application_crawler,
            normalizer=ContentNormalizer(),
            change_detector=DeterministicChangeDetector(application_settings),
            signal_builder=DeterministicSignalBuilder(),
            source_discovery=source_discovery,
            source_service=source_service,
            settings=application_settings,
        )
        scan_runner = GraphCompetitorScanRunner(application_persistence, scan_graph)
        if competitor_scan_executor is not None:
            scan_executor = competitor_scan_executor
        elif application_settings.background_jobs_enabled:
            scan_executor = DurableCompetitorScanExecutor(job_queue, application_persistence)
        else:
            scan_executor = InProcessCompetitorScanExecutor(scan_runner)
        scan_service = CompetitorScanService(application_persistence, scan_executor)
        monitoring_schedule_service = MonitoringScheduleService(
            application_persistence,
            scan_service,
            application_settings,
        )
        graph = CompanyInitializationGraph(
            persistence=application_persistence,
            crawler=application_crawler,
            normalizer=ContentNormalizer(),
            extractor=application_extractor,
            settings=application_settings,
        )
        discovery_runner = CompetitorDiscoveryRunner(
            application_persistence,
            application_search_provider,
            application_settings,
            candidate_classifier,
        )
        discovery_executor = discovery_background_executor or InProcessBackgroundExecutor(
            discovery_runner
        )
        discovery_service = CompetitorDiscoveryService(
            application_persistence,
            discovery_executor,
            scan_service if application_settings.auto_start_competitor_baseline else None,
            monitoring_schedule_service,
        )
        runner = CompanyInitializationRunner(
            application_persistence,
            graph,
            discovery_service if application_settings.auto_start_competitor_discovery else None,
        )
        if background_executor is not None:
            executor = background_executor
            rebuild_executor = background_executor
        elif application_settings.background_jobs_enabled:
            executor = DurableJobExecutor(
                job_queue,
                application_persistence,
                JobType.COMPANY_INITIALIZATION,
                "initialization_job",
            )
            rebuild_executor = DurableJobExecutor(
                job_queue,
                application_persistence,
                JobType.COMPANY_INTELLIGENCE_REBUILD,
                "initialization_job",
            )
        else:
            executor = InProcessBackgroundExecutor(runner)
            rebuild_executor = executor
        application.state.persistence = application_persistence
        application.state.background_job_queue = job_queue
        application.state.evidence_indexing_service = evidence_indexing_service
        application.state.evidence_indexing_scheduler = evidence_indexing_scheduler
        application.state.background_executor = executor
        application.state.initialization_runner = runner
        application.state.discovery_runner = discovery_runner
        application.state.discovery_background_executor = discovery_executor
        application.state.discovery_service = discovery_service
        application.state.competitor_source_service = source_service
        application.state.competitor_scan_runner = scan_runner
        application.state.competitor_scan_executor = scan_executor
        application.state.competitor_scan_service = scan_service
        application.state.competitor_signal_service = CompetitorSignalService(
            application_persistence
        )
        application.state.company_document_service = document_service
        application.state.document_storage = application_document_storage
        application.state.vector_store = active_vectors
        application.state.embedding_provider = application_embeddings
        application.state.document_ingestion_runner = document_runner
        application.state.document_background_executor = document_executor
        application.state.background_job_service = BackgroundJobService(
            application_persistence, job_queue
        )
        application.state.monitoring_schedule_service = monitoring_schedule_service
        application.state.notification_service = NotificationService(application_persistence)
        application.state.intelligence_search_service = IntelligenceSearchService(
            persistence=application_persistence,
            embeddings=application_embeddings,
            vectors=active_vectors,
            settings=application_settings,
        )
        application.state.intelligence_reindex_service = IntelligenceReindexService(
            application_persistence, job_queue
        )
        application.state.onboarding_service = CompanyOnboardingService(
            application_persistence,
            executor,
            document_service,
            rebuild_executor,
        )
        application.state.ready = True
        logger.info("application_started", environment=application_settings.app_env)
        try:
            yield
        finally:
            application.state.ready = False
            await executor.close()
            if rebuild_executor is not executor:
                await rebuild_executor.close()
            if discovery_executor is not executor:
                await discovery_executor.close()
            await scan_executor.close()
            await document_executor.close()
            await active_vectors.close()
            await application_persistence.close()
            logger.info("application_stopped")

    application = FastAPI(
        title=application_settings.app_name,
        # API failures always use the stable envelope, even in local development.
        debug=False,
        lifespan=lifespan,
        docs_url="/docs" if application_settings.docs_enabled else None,
        redoc_url="/redoc" if application_settings.docs_enabled else None,
        openapi_url="/openapi.json" if application_settings.docs_enabled else None,
    )
    application.state.settings = application_settings
    application.state.authentication_verifier = application_authentication_verifier
    application.state.ready = False
    application.state.persistence = application_persistence
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=application_settings.frontend_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
    )
    application.include_router(health_router)
    application.include_router(api_v1_router, prefix=application_settings.api_v1_prefix)
    register_error_handlers(application)
    return application


app = create_app()
