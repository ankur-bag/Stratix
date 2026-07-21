from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.health import router as health_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.middleware import RequestContextMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure a Stratix API application instance."""
    application_settings = settings or get_settings()
    configure_logging(application_settings.log_level)
    logger = get_logger(__name__)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.ready = True
        logger.info("application_started", environment=application_settings.app_env)
        try:
            yield
        finally:
            application.state.ready = False
            logger.info("application_stopped")

    application = FastAPI(
        title=application_settings.app_name,
        # API failures always use the stable envelope, even in local development.
        debug=False,
        lifespan=lifespan,
    )
    application.state.settings = application_settings
    application.state.ready = False
    application.add_middleware(RequestContextMiddleware)
    application.include_router(health_router)
    register_error_handlers(application)
    return application


app = create_app()
