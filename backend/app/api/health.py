from datetime import timedelta
from typing import Literal, cast

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from app.api.errors import AppError, ErrorEnvelope
from app.core.clock import SystemClock
from app.core.config import Settings
from app.domain.background_jobs import QueueHealth, ServiceType
from app.repositories.interfaces import Persistence


class IntelligenceHealth(BaseModel):
    indexing_enabled: bool
    indexing_required: bool
    embeddings_available: bool
    vector_store_available: bool


class HealthResponse(BaseModel):
    """Health endpoint response."""

    status: Literal["alive", "ready"]
    queue: QueueHealth | None = None
    intelligence: IntelligenceHealth | None = None


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse, response_model_exclude_none=True)
async def live() -> HealthResponse:
    """Confirm that the API process can serve requests."""
    return HealthResponse(status="alive")


@router.get(
    "/ready",
    response_model=HealthResponse,
    response_model_exclude_none=True,
    responses={
        503: {
            "description": "Application startup is incomplete",
            "model": ErrorEnvelope,
        }
    },
)
async def ready(request: Request) -> HealthResponse:
    """Confirm that all currently required dependencies are initialized."""
    persistence = cast(Persistence, request.app.state.persistence)
    settings = cast(Settings, request.app.state.settings)
    if settings.app_env == "production" and not settings.clerk_verification_configured:
        raise AppError(
            code="AUTH_CONFIGURATION_INVALID",
            message="Authentication is not configured for this deployment.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not request.app.state.ready or not await persistence.ping():
        raise AppError(
            code="DATABASE_UNAVAILABLE",
            message="The database is temporarily unavailable.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    embeddings_available = bool(
        getattr(request.app.state, "embedding_provider", None)
        and request.app.state.embedding_provider.enabled
    )
    vector_store_available = bool(
        getattr(request.app.state, "vector_store", None) and request.app.state.vector_store.enabled
    )
    intelligence = IntelligenceHealth(
        indexing_enabled=settings.competitor_evidence_indexing_enabled,
        indexing_required=settings.competitor_evidence_indexing_required,
        embeddings_available=embeddings_available,
        vector_store_available=vector_store_available,
    )
    if settings.competitor_evidence_indexing_required and not (
        embeddings_available and vector_store_available
    ):
        raise AppError(
            code="INTELLIGENCE_PROVIDER_UNAVAILABLE",
            message="Required competitor intelligence indexing is unavailable.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not settings.background_jobs_enabled:
        return HealthResponse(status="ready", intelligence=intelligence)
    clock = SystemClock()
    now = clock.now()
    worker_available = await persistence.service_instances.has_fresh(
        ServiceType.WORKER,
        now - timedelta(seconds=settings.worker_heartbeat_ttl_seconds),
    )
    scheduler_available = await persistence.service_instances.has_fresh(
        ServiceType.SCHEDULER,
        now - timedelta(seconds=settings.scheduler_heartbeat_ttl_seconds),
    )
    oldest, expired, dead = await persistence.background_jobs.operational_counts(now)
    if settings.require_active_worker and not worker_available:
        raise AppError(
            code="WORKER_UNAVAILABLE",
            message="No background worker is currently available.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if settings.require_active_scheduler and not scheduler_available:
        raise AppError(
            code="SCHEDULER_UNAVAILABLE",
            message="No monitoring scheduler is currently available.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return HealthResponse(
        status="ready",
        intelligence=intelligence,
        queue=QueueHealth(
            worker_available=worker_available,
            scheduler_available=scheduler_available,
            queue_degraded=expired > 0
            or (
                settings.background_jobs_enabled
                and oldest is not None
                and oldest > settings.worker_heartbeat_ttl_seconds
                and not worker_available
            ),
            oldest_queued_job_age_seconds=oldest,
            expired_lease_count=expired,
            dead_letter_count=dead,
        ),
    )
