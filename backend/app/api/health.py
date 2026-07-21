from typing import Literal, cast

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from app.api.errors import AppError, ErrorEnvelope
from app.core.config import Settings
from app.repositories.interfaces import Persistence


class HealthResponse(BaseModel):
    """Health endpoint response."""

    status: Literal["alive", "ready"]


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    """Confirm that the API process can serve requests."""
    return HealthResponse(status="alive")


@router.get(
    "/ready",
    response_model=HealthResponse,
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
    return HealthResponse(status="ready")
