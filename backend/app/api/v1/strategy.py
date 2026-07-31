from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.dependencies import Authenticated, StrategicAnalysisService
from app.domain.intelligence_evidence import EvidenceExcerpt
from app.domain.strategy import (
    StrategicReportFeedbackCreate,
    StrategicReportFeedbackResponse,
    StrategicReportPage,
    StrategicReportView,
    StrategyRunCreate,
    StrategyRunPage,
    StrategyRunResponse,
    StrategyRunStatus,
)

router = APIRouter(prefix="/strategy")


@router.post(
    "/runs",
    response_model=StrategyRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_strategy_run(
    request: StrategyRunCreate,
    context: Authenticated,
    service: StrategicAnalysisService,
) -> StrategyRunResponse:
    return await service.create_run(context, request)


@router.get("/runs", response_model=StrategyRunPage)
async def list_strategy_runs(
    context: Authenticated,
    service: StrategicAnalysisService,
    run_status: StrategyRunStatus | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> StrategyRunPage:
    return await service.list_runs(context, run_status=run_status, cursor=cursor, limit=limit)


@router.get("/runs/{run_id}", response_model=StrategyRunResponse)
async def get_strategy_run(
    run_id: str,
    context: Authenticated,
    service: StrategicAnalysisService,
) -> StrategyRunResponse:
    return await service.get_run(context, run_id)


@router.post("/runs/{run_id}/cancel", response_model=StrategyRunResponse)
async def cancel_strategy_run(
    run_id: str,
    context: Authenticated,
    service: StrategicAnalysisService,
) -> StrategyRunResponse:
    return await service.cancel_run(context, run_id)


@router.post(
    "/runs/{run_id}/retry",
    response_model=StrategyRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_strategy_run(
    run_id: str,
    context: Authenticated,
    service: StrategicAnalysisService,
) -> StrategyRunResponse:
    return await service.retry_run(context, run_id)


@router.get("/reports", response_model=StrategicReportPage)
async def list_strategy_reports(
    context: Authenticated,
    service: StrategicAnalysisService,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> StrategicReportPage:
    return await service.list_reports(context, cursor=cursor, limit=limit)


@router.get("/reports/{report_id}", response_model=StrategicReportView)
async def get_strategy_report(
    report_id: str,
    context: Authenticated,
    service: StrategicAnalysisService,
) -> StrategicReportView:
    return await service.get_report(context, report_id)


@router.get(
    "/reports/{report_id}/citations/{evidence_id}",
    response_model=EvidenceExcerpt,
)
async def get_strategy_report_citation(
    report_id: str,
    evidence_id: str,
    context: Authenticated,
    service: StrategicAnalysisService,
) -> EvidenceExcerpt:
    return await service.citation(context, report_id, evidence_id)


@router.post(
    "/reports/{report_id}/feedback",
    response_model=StrategicReportFeedbackResponse,
)
async def submit_strategy_report_feedback(
    report_id: str,
    request: StrategicReportFeedbackCreate,
    context: Authenticated,
    service: StrategicAnalysisService,
) -> StrategicReportFeedbackResponse:
    feedback = await service.submit_feedback(context, report_id, request)
    return StrategicReportFeedbackResponse.from_feedback(feedback)
