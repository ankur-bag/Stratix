from typing import Annotated

from fastapi import APIRouter, Header, status

from app.api.dependencies import Authenticated, OnboardingService
from app.domain.intelligence import (
    CompanyIntelligenceProfile,
    ReinitializeResponse,
    Snapshot,
)
from app.domain.onboarding import (
    CompanyOnboardingRequest,
    CompanyProfile,
    CompanyUpdateRequest,
    InitializationJob,
    OnboardingResponse,
    OnboardingStatusResponse,
)

router = APIRouter()


@router.post(
    "/onboarding/company",
    response_model=OnboardingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def onboard_company(
    request: CompanyOnboardingRequest,
    context: Authenticated,
    service: OnboardingService,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> OnboardingResponse:
    """Persist a company submission and queue deterministic initialization."""
    return await service.submit(context=context, idempotency_key=idempotency_key, request=request)


@router.get("/company", response_model=CompanyProfile)
async def get_company(context: Authenticated, service: OnboardingService) -> CompanyProfile:
    """Return the authenticated organization's primary company profile."""
    return await service.get_company(context)


@router.patch("/company", response_model=CompanyProfile)
async def update_company(
    request: CompanyUpdateRequest,
    context: Authenticated,
    service: OnboardingService,
) -> CompanyProfile:
    """Update editable company profile fields."""
    return await service.update_company(context, request)


@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    context: Authenticated, service: OnboardingService
) -> OnboardingStatusResponse:
    """Return organization onboarding state and its latest job."""
    return await service.get_onboarding_status(context)


@router.get("/initialization-jobs/{job_id}", response_model=InitializationJob)
async def get_initialization_job(
    job_id: str, context: Authenticated, service: OnboardingService
) -> InitializationJob:
    """Return one organization-scoped initialization job."""
    return await service.get_job(context, job_id)


@router.get("/company/intelligence-profile", response_model=CompanyIntelligenceProfile)
async def get_company_intelligence_profile(
    context: Authenticated, service: OnboardingService
) -> CompanyIntelligenceProfile:
    """Return the latest observed company intelligence profile."""
    return await service.get_intelligence_profile(context)


@router.get("/company/snapshots", response_model=list[Snapshot])
async def list_company_snapshots(
    context: Authenticated, service: OnboardingService
) -> list[Snapshot]:
    """Return organization-scoped company website baselines."""
    return await service.get_snapshots(context)


@router.post(
    "/company/reinitialize",
    response_model=ReinitializeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reinitialize_company(
    context: Authenticated,
    service: OnboardingService,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ReinitializeResponse:
    """Queue a new baseline without deleting prior snapshots."""
    return await service.reinitialize(context=context, idempotency_key=idempotency_key)
