from typing import Annotated, cast

from fastapi import Depends, Request, status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext, AuthenticationFailure, AuthenticationVerifier
from app.services.discovery import CompetitorDiscoveryService
from app.services.onboarding import CompanyOnboardingService


async def get_authenticated_context(request: Request) -> AuthenticatedContext:
    """Resolve a trusted identity through the configured verifier."""
    verifier = cast(AuthenticationVerifier, request.app.state.authentication_verifier)
    try:
        return verifier.authenticate(request)
    except AuthenticationFailure as exc:
        raise AppError(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
        ) from exc


async def require_authenticated_user(
    context: Annotated[AuthenticatedContext, Depends(get_authenticated_context)],
) -> AuthenticatedContext:
    """Require a verified Clerk user or the explicit development identity."""
    return context


async def require_active_organization(
    context: Annotated[AuthenticatedContext, Depends(require_authenticated_user)],
) -> AuthenticatedContext:
    """Require a verified active organization for tenant-owned operations."""
    if not context.organization_id:
        raise AppError(
            code="ORGANIZATION_REQUIRED",
            message="Select an active organization to continue.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return context


async def require_organization_admin(
    context: Annotated[AuthenticatedContext, Depends(require_active_organization)],
) -> AuthenticatedContext:
    """Require the active Organization's Clerk administrator role."""
    if context.organization_role not in {"org:admin", "admin"}:
        raise AppError(
            code="INSUFFICIENT_PERMISSION",
            message="Organization administrator access is required.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return context


def get_onboarding_service(request: Request) -> CompanyOnboardingService:
    """Return the application-scoped onboarding service."""
    return cast(CompanyOnboardingService, request.app.state.onboarding_service)


def get_discovery_service(request: Request) -> CompetitorDiscoveryService:
    """Return the application-scoped competitor discovery service."""
    return cast(CompetitorDiscoveryService, request.app.state.discovery_service)


Authenticated = Annotated[AuthenticatedContext, Depends(require_active_organization)]
OnboardingService = Annotated[CompanyOnboardingService, Depends(get_onboarding_service)]
DiscoveryService = Annotated[CompetitorDiscoveryService, Depends(get_discovery_service)]
