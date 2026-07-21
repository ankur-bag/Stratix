from fastapi import APIRouter

from app.api.v1.discovery import router as discovery_router
from app.api.v1.onboarding import router as onboarding_router

router = APIRouter()
router.include_router(onboarding_router, tags=["company-onboarding"])
router.include_router(discovery_router, tags=["competitor-discovery"])
