from fastapi import APIRouter

from app.api.v1.company_documents import router as company_documents_router
from app.api.v1.competitor_scans import router as competitor_scans_router
from app.api.v1.competitor_sources import router as competitor_sources_router
from app.api.v1.discovery import router as discovery_router
from app.api.v1.onboarding import router as onboarding_router
from app.api.v1.signals import router as signals_router

router = APIRouter()
router.include_router(onboarding_router, tags=["company-onboarding"])
router.include_router(company_documents_router, tags=["company-knowledge"])
router.include_router(discovery_router, tags=["competitor-discovery"])
router.include_router(competitor_sources_router, tags=["competitor-sources"])
router.include_router(competitor_scans_router, tags=["competitor-scans"])
router.include_router(signals_router, tags=["competitor-signals"])
