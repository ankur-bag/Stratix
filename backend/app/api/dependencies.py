from typing import Annotated, cast

from fastapi import Depends, Request, status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext, AuthenticationFailure, AuthenticationVerifier
from app.services.company_documents import CompanyDocumentService
from app.services.competitor_sources import CompetitorSourceService
from app.services.discovery import CompetitorDiscoveryService
from app.services.onboarding import CompanyOnboardingService
from app.services.scanning import CompetitorScanService
from app.services.signals import CompetitorSignalService


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


def get_competitor_source_service(request: Request) -> CompetitorSourceService:
    return cast(CompetitorSourceService, request.app.state.competitor_source_service)


def get_competitor_scan_service(request: Request) -> CompetitorScanService:
    return cast(CompetitorScanService, request.app.state.competitor_scan_service)


def get_signal_service(request: Request) -> CompetitorSignalService:
    return cast(CompetitorSignalService, request.app.state.competitor_signal_service)


def get_company_document_service(request: Request) -> CompanyDocumentService:
    return cast(CompanyDocumentService, request.app.state.company_document_service)


Authenticated = Annotated[AuthenticatedContext, Depends(require_active_organization)]
OnboardingService = Annotated[CompanyOnboardingService, Depends(get_onboarding_service)]
DiscoveryService = Annotated[CompetitorDiscoveryService, Depends(get_discovery_service)]
CompetitorSourcesService = Annotated[
    CompetitorSourceService, Depends(get_competitor_source_service)
]
CompetitorScansService = Annotated[CompetitorScanService, Depends(get_competitor_scan_service)]
SignalsService = Annotated[CompetitorSignalService, Depends(get_signal_service)]
CompanyDocumentsService = Annotated[CompanyDocumentService, Depends(get_company_document_service)]
