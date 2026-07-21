from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.health import router as health_router
from app.api.v1.router import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.middleware import RequestContextMiddleware
from app.repositories.interfaces import Persistence
from app.repositories.mongo import MongoPersistence
from app.services.initialization import (
    BackgroundExecutor,
    CompanyInitializationRunner,
    InProcessBackgroundExecutor,
)
from app.services.onboarding import CompanyOnboardingService


def create_app(
    settings: Settings | None = None,
    *,
    persistence: Persistence | None = None,
    background_executor: BackgroundExecutor | None = None,
) -> FastAPI:
    """Create and configure a Stratix API application instance."""
    application_settings = settings or get_settings()
    configure_logging(application_settings.log_level)
    logger = get_logger(__name__)
    application_persistence = persistence or cast(
        Persistence, MongoPersistence(application_settings)
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await application_persistence.start()
        runner = CompanyInitializationRunner(application_persistence)
        executor = background_executor or InProcessBackgroundExecutor(runner)
        application.state.persistence = application_persistence
        application.state.background_executor = executor
        application.state.initialization_runner = runner
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
            await application_persistence.close()
            logger.info("application_stopped")

    application = FastAPI(
        title=application_settings.app_name,
        # API failures always use the stable envelope, even in local development.
        debug=False,
        lifespan=lifespan,
    )
    application.state.settings = application_settings
    application.state.ready = False
    application.state.persistence = application_persistence
    application.add_middleware(RequestContextMiddleware)
    application.include_router(health_router)
    application.include_router(api_v1_router, prefix=application_settings.api_v1_prefix)
    register_error_handlers(application)
    return application


app = create_app()
