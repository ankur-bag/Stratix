from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import (
    Authenticated,
    IntelligenceReindex,
    IntelligenceSearch,
    require_organization_admin,
)
from app.core.auth import AuthenticatedContext
from app.domain.intelligence_evidence import (
    CompetitorTimelinePage,
    EvidenceSourceType,
    IntelligenceAnswerRequest,
    IntelligenceAnswerResponse,
    IntelligenceCategory,
    IntelligenceIndexingStatus,
    IntelligenceOverview,
    IntelligenceReindexRequest,
    IntelligenceReindexResponse,
    IntelligenceSearchRequest,
    IntelligenceSearchResponse,
    IntelligenceTrend,
)

router = APIRouter()


@router.post("/intelligence/search", response_model=IntelligenceSearchResponse)
async def search_intelligence(
    request: IntelligenceSearchRequest,
    context: Authenticated,
    service: IntelligenceSearch,
) -> IntelligenceSearchResponse:
    return await service.search(context, request)


@router.post("/intelligence/answer", response_model=IntelligenceAnswerResponse)
async def answer_intelligence(
    request: IntelligenceAnswerRequest,
    context: Authenticated,
    service: IntelligenceSearch,
) -> IntelligenceAnswerResponse:
    return await service.answer(context, request)


@router.get("/competitors/{competitor_id}/timeline", response_model=CompetitorTimelinePage)
async def competitor_timeline(
    competitor_id: str,
    context: Authenticated,
    service: IntelligenceSearch,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    category: Annotated[list[IntelligenceCategory] | None, Query()] = None,
    source_type: Annotated[list[EvidenceSourceType] | None, Query()] = None,
    observed_after: datetime | None = None,
    observed_before: datetime | None = None,
    order: Literal["asc", "desc"] = "desc",
) -> CompetitorTimelinePage:
    return await service.timeline(
        context,
        competitor_id,
        cursor=cursor,
        limit=limit,
        categories=category,
        source_types=source_type,
        observed_after=observed_after,
        observed_before=observed_before,
        ascending=order == "asc",
    )


@router.get("/intelligence/overview", response_model=IntelligenceOverview)
async def intelligence_overview(
    context: Authenticated, service: IntelligenceSearch
) -> IntelligenceOverview:
    return await service.overview(context)


@router.get("/intelligence/trends", response_model=list[IntelligenceTrend])
async def intelligence_trends(
    context: Authenticated,
    service: IntelligenceSearch,
    window_days: Literal[30, 90, 180, 365] = 180,
) -> list[IntelligenceTrend]:
    return await service.trends(context, window_days=window_days)


@router.post(
    "/intelligence/reindex",
    response_model=IntelligenceReindexResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_intelligence(
    request: IntelligenceReindexRequest,
    context: Annotated[AuthenticatedContext, Depends(require_organization_admin)],
    service: IntelligenceReindex,
) -> IntelligenceReindexResponse:
    return await service.schedule(context, request)


@router.get("/intelligence/indexing-status", response_model=IntelligenceIndexingStatus)
async def intelligence_indexing_status(
    context: Authenticated, service: IntelligenceSearch
) -> IntelligenceIndexingStatus:
    return await service.indexing_status(context)
