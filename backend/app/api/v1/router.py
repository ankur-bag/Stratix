from fastapi import APIRouter

from app.api.v1.background_jobs import router as background_jobs_router
from app.api.v1.company_documents import router as company_documents_router
from app.api.v1.competitor_scans import router as competitor_scans_router
from app.api.v1.competitor_sources import router as competitor_sources_router
from app.api.v1.discovery import router as discovery_router
from app.api.v1.intelligence import router as intelligence_router
from app.api.v1.monitoring_schedules import router as monitoring_schedules_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.onboarding import router as onboarding_router
from app.api.v1.signals import router as signals_router
from app.api.v1.strategy import router as strategy_router

router = APIRouter()
router.include_router(onboarding_router, tags=["company-onboarding"])
router.include_router(company_documents_router, tags=["company-knowledge"])
router.include_router(discovery_router, tags=["competitor-discovery"])
router.include_router(competitor_sources_router, tags=["competitor-sources"])
router.include_router(competitor_scans_router, tags=["competitor-scans"])
router.include_router(signals_router, tags=["competitor-signals"])
router.include_router(background_jobs_router, tags=["background-jobs"])
router.include_router(monitoring_schedules_router, tags=["monitoring-schedules"])
router.include_router(notifications_router, tags=["notifications"])
router.include_router(intelligence_router, tags=["intelligence"])
router.include_router(strategy_router, tags=["strategy"])
