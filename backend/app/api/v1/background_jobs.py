from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import Authenticated, BackgroundJobsService
from app.domain.background_jobs import (
    BackgroundJob,
    BackgroundJobPage,
    BackgroundJobStatus,
    RetryJobResponse,
)

router = APIRouter()


@router.get("/jobs", response_model=BackgroundJobPage)
async def list_jobs(
    context: Authenticated,
    service: BackgroundJobsService,
    job_status: BackgroundJobStatus | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> BackgroundJobPage:
    return await service.list(context, job_status=job_status, cursor=cursor, limit=limit)


@router.get("/jobs/{job_id}", response_model=BackgroundJob)
async def get_job(
    job_id: str, context: Authenticated, service: BackgroundJobsService
) -> BackgroundJob:
    return await service.get(context, job_id)


@router.post("/jobs/{job_id}/cancel", response_model=BackgroundJob)
async def cancel_job(
    job_id: str, context: Authenticated, service: BackgroundJobsService
) -> BackgroundJob:
    return await service.cancel(context, job_id)


@router.post("/jobs/{job_id}/retry", response_model=RetryJobResponse)
async def retry_job(
    job_id: str, context: Authenticated, service: BackgroundJobsService
) -> RetryJobResponse:
    return await service.retry(context, job_id)
