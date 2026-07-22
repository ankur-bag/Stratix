from typing import Annotated

from fastapi import APIRouter, Header, Query, status

from app.api.dependencies import Authenticated, CompetitorScansService
from app.domain.scanning import (
    CompetitorScanJob,
    CompetitorScanPage,
    CompetitorScanRequest,
    CompetitorScanResponse,
    ScanType,
)

router = APIRouter()


@router.post(
    "/competitors/{competitor_id}/scans",
    response_model=CompetitorScanResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_scan(
    competitor_id: str,
    request: CompetitorScanRequest,
    context: Authenticated,
    service: CompetitorScansService,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> CompetitorScanResponse:
    return await service.start(
        context=context,
        competitor_id=competitor_id,
        idempotency_key=idempotency_key,
        request=request,
    )


@router.post(
    "/competitors/{competitor_id}/sources/discover",
    response_model=CompetitorScanResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def discover_sources(
    competitor_id: str,
    context: Authenticated,
    service: CompetitorScansService,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> CompetitorScanResponse:
    return await service.start(
        context=context,
        competitor_id=competitor_id,
        idempotency_key=idempotency_key,
        request=CompetitorScanRequest(scan_type=ScanType.BASELINE, rediscover_sources=True),
    )


@router.get("/competitor-scans/{scan_id}", response_model=CompetitorScanJob)
async def get_scan(
    scan_id: str, context: Authenticated, service: CompetitorScansService
) -> CompetitorScanJob:
    return await service.get(context, scan_id)


@router.get("/competitors/{competitor_id}/scans", response_model=CompetitorScanPage)
async def list_scans(
    competitor_id: str,
    context: Authenticated,
    service: CompetitorScansService,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> CompetitorScanPage:
    return await service.list(context, competitor_id, cursor=cursor, limit=limit)
