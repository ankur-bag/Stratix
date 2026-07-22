from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.dependencies import Authenticated, CompetitorSourcesService
from app.domain.monitoring import (
    CompetitorSource,
    CompetitorSourceCreate,
    CompetitorSourcePage,
    CompetitorSourceUpdate,
)

router = APIRouter()


@router.get("/competitors/{competitor_id}/sources", response_model=CompetitorSourcePage)
async def list_sources(
    competitor_id: str,
    context: Authenticated,
    service: CompetitorSourcesService,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> CompetitorSourcePage:
    return await service.list_sources(context, competitor_id, cursor=cursor, limit=limit)


@router.post(
    "/competitors/{competitor_id}/sources",
    response_model=CompetitorSource,
    status_code=status.HTTP_201_CREATED,
)
async def create_source(
    competitor_id: str,
    request: CompetitorSourceCreate,
    context: Authenticated,
    service: CompetitorSourcesService,
) -> CompetitorSource:
    return await service.create(context, competitor_id, request)


@router.patch("/competitors/{competitor_id}/sources/{source_id}", response_model=CompetitorSource)
async def update_source(
    competitor_id: str,
    source_id: str,
    request: CompetitorSourceUpdate,
    context: Authenticated,
    service: CompetitorSourcesService,
) -> CompetitorSource:
    return await service.update(context, competitor_id, source_id, request)


@router.delete(
    "/competitors/{competitor_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disable_source(
    competitor_id: str,
    source_id: str,
    context: Authenticated,
    service: CompetitorSourcesService,
) -> Response:
    await service.disable(context, competitor_id, source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
