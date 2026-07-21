from typing import Annotated, cast

from fastapi import Depends, Request, status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.services.onboarding import CompanyOnboardingService


async def get_authenticated_context(request: Request) -> AuthenticatedContext:
    """Resolve a trusted identity, ready for replacement by Clerk verification."""
    settings = cast(Settings, request.app.state.settings)
    if settings.app_env == "development" and settings.auth_disabled:
        return AuthenticatedContext(
            user_id=settings.dev_user_id,
            organization_id=settings.dev_organization_id,
        )
    raise AppError(
        code="AUTHENTICATION_REQUIRED",
        message="Authentication is required.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def get_onboarding_service(request: Request) -> CompanyOnboardingService:
    """Return the application-scoped onboarding service."""
    return cast(CompanyOnboardingService, request.app.state.onboarding_service)


Authenticated = Annotated[AuthenticatedContext, Depends(get_authenticated_context)]
OnboardingService = Annotated[CompanyOnboardingService, Depends(get_onboarding_service)]
