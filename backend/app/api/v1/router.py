from fastapi import APIRouter

from app.api.v1.onboarding import router as onboarding_router

router = APIRouter()
router.include_router(onboarding_router, tags=["company-onboarding"])
