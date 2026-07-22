from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import Authenticated, SignalsService
from app.domain.signals import (
    CompetitorSignal,
    SignalPage,
    SignalStatus,
    SignalStatusUpdate,
    SignalType,
)

router = APIRouter()


@router.get("/competitors/{competitor_id}/signals", response_model=SignalPage)
async def list_signals(
    competitor_id: str,
    context: Authenticated,
    service: SignalsService,
    signal_type: Annotated[SignalType | None, Query(alias="type")] = None,
    signal_status: Annotated[SignalStatus | None, Query(alias="status")] = None,
    minimum_importance: Annotated[float, Query(ge=0, le=1)] = 0,
    source_id: str | None = None,
    observed_after: datetime | None = None,
    observed_before: datetime | None = None,
    scan_id: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> SignalPage:
    return await service.list(
        context,
        competitor_id,
        signal_type=signal_type,
        signal_status=signal_status,
        minimum_importance=minimum_importance,
        source_id=source_id,
        observed_after=observed_after,
        observed_before=observed_before,
        scan_id=scan_id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/signals/{signal_id}", response_model=CompetitorSignal)
async def get_signal(
    signal_id: str, context: Authenticated, service: SignalsService
) -> CompetitorSignal:
    return await service.get(context, signal_id)


@router.patch("/signals/{signal_id}", response_model=CompetitorSignal)
async def update_signal(
    signal_id: str,
    request: SignalStatusUpdate,
    context: Authenticated,
    service: SignalsService,
) -> CompetitorSignal:
    return await service.update_status(context, signal_id, request)
