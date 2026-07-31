from fastapi import APIRouter, status

from app.api.dependencies import Authenticated, MonitoringSchedulesService
from app.domain.background_jobs import MonitoringSchedule, MonitoringScheduleUpdate
from app.domain.scanning import CompetitorScanResponse

router = APIRouter()


@router.get(
    "/competitors/{competitor_id}/monitoring-schedule",
    response_model=MonitoringSchedule,
)
async def get_monitoring_schedule(
    competitor_id: str,
    context: Authenticated,
    service: MonitoringSchedulesService,
) -> MonitoringSchedule:
    return await service.get(context, competitor_id)


@router.patch(
    "/competitors/{competitor_id}/monitoring-schedule",
    response_model=MonitoringSchedule,
)
async def update_monitoring_schedule(
    competitor_id: str,
    payload: MonitoringScheduleUpdate,
    context: Authenticated,
    service: MonitoringSchedulesService,
) -> MonitoringSchedule:
    return await service.update(context, competitor_id, payload)


@router.post(
    "/competitors/{competitor_id}/monitoring-schedule/run-now",
    response_model=CompetitorScanResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_monitoring_now(
    competitor_id: str,
    context: Authenticated,
    service: MonitoringSchedulesService,
) -> CompetitorScanResponse:
    return await service.run_now(context, competitor_id)
