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
from app.graphs.company_initialization import CompanyInitializationGraph
from app.graphs.competitor_scan import CompetitorScanGraph
from app.middleware import RequestContextMiddleware
from app.repositories.interfaces import Persistence
from app.repositories.mongo import MongoPersistence
from app.services.change_detection import DeterministicChangeDetector
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
from app.services.normalization import ContentNormalizer
from app.services.onboarding import CompanyOnboardingService
from app.services.scanning import (
    CompetitorScanExecutor,
    CompetitorScanService,
    GraphCompetitorScanRunner,
    InProcessCompetitorScanExecutor,
)
from app.services.signals import CompetitorSignalService, DeterministicSignalBuilder


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

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await application_persistence.start()
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
        scan_executor = competitor_scan_executor or InProcessCompetitorScanExecutor(scan_runner)
        scan_service = CompetitorScanService(application_persistence, scan_executor)
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
        )
        runner = CompanyInitializationRunner(
            application_persistence,
            graph,
            discovery_service if application_settings.auto_start_competitor_discovery else None,
        )
        executor = background_executor or InProcessBackgroundExecutor(runner)
        application.state.persistence = application_persistence
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
        application.state.onboarding_service = CompanyOnboardingService(
            application_persistence, executor
        )
        application.state.ready = True
        logger.info("application_started", environment=application_settings.app_env)
        try:
            yield
        finally:
            application.state.ready = False
            await executor.close()
            if discovery_executor is not executor:
                await discovery_executor.close()
            await scan_executor.close()
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
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
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
