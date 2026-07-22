from typing import Annotated

from fastapi import APIRouter, Header, Query, status

from app.api.dependencies import Authenticated, DiscoveryService
from app.domain.discovery import (
    CandidatePage,
    CandidateReviewRequest,
    CandidateReviewResponse,
    CandidateStatus,
    Competitor,
    CompetitorCandidate,
    DiscoveryJob,
    DiscoveryJobResponse,
    RelationshipType,
)

router = APIRouter()


@router.post(
    "/competitor-discovery/jobs",
    response_model=DiscoveryJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_discovery(
    context: Authenticated,
    service: DiscoveryService,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> DiscoveryJobResponse:
    return await service.start(context=context, idempotency_key=idempotency_key)


@router.get("/competitor-discovery/jobs/latest", response_model=DiscoveryJob)
async def get_latest_discovery_job(
    context: Authenticated, service: DiscoveryService
) -> DiscoveryJob:
    return await service.get_latest_job(context)


@router.get("/competitor-discovery/jobs/{job_id}", response_model=DiscoveryJob)
async def get_discovery_job(
    job_id: str, context: Authenticated, service: DiscoveryService
) -> DiscoveryJob:
    return await service.get_job(context, job_id)


@router.get("/competitor-candidates", response_model=CandidatePage)
async def list_candidates(
    context: Authenticated,
    service: DiscoveryService,
    candidate_status: Annotated[CandidateStatus | None, Query(alias="status")] = None,
    relationship_type: RelationshipType | None = None,
    minimum_score: Annotated[float, Query(ge=0, le=1)] = 0,
    discovery_job_id: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> CandidatePage:
    return await service.list_candidates(
        context,
        candidate_status=candidate_status,
        relationship_type=relationship_type,
        minimum_score=minimum_score,
        discovery_job_id=discovery_job_id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/competitor-candidates/{candidate_id}", response_model=CompetitorCandidate)
async def get_candidate(
    candidate_id: str, context: Authenticated, service: DiscoveryService
) -> CompetitorCandidate:
    return await service.get_candidate(context, candidate_id)


@router.post("/competitor-candidates/review", response_model=CandidateReviewResponse)
async def review_candidates(
    request: CandidateReviewRequest,
    context: Authenticated,
    service: DiscoveryService,
) -> CandidateReviewResponse:
    return await service.review(context, request)


@router.get("/competitors", response_model=list[Competitor])
async def list_competitors(context: Authenticated, service: DiscoveryService) -> list[Competitor]:
    return await service.list_competitors(context)


@router.get("/competitors/{competitor_id}", response_model=Competitor)
async def get_competitor(
    competitor_id: str, context: Authenticated, service: DiscoveryService
) -> Competitor:
    return await service.get_competitor(context, competitor_id)
