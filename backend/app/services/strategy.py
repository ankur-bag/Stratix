from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, NotRequired, TypedDict

from fastapi import status
from langgraph.graph import END, START, StateGraph

from app.agents.errors import StrategicAgentError
from app.agents.strategic import StrategicAgent
from app.agents.validation import (
    cited_evidence_ids,
    deterministic_fallback_report,
    validate_strategic_report,
)
from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.background_jobs import (
    BackgroundJob,
    BackgroundJobStatus,
    JobExecutionResult,
    JobType,
)
from app.domain.discovery import Competitor, CompetitorStatus
from app.domain.intelligence_evidence import (
    EvidenceExcerpt,
    IntelligenceEvidence,
)
from app.domain.onboarding import new_id
from app.domain.strategy import (
    ACTIVE_STRATEGY_RUN_STATUSES,
    StrategicAgentContext,
    StrategicAgentRequest,
    StrategicAgentResult,
    StrategicEvidenceCoverage,
    StrategicReport,
    StrategicReportFeedback,
    StrategicReportFeedbackCreate,
    StrategicReportPage,
    StrategicReportView,
    StrategyMode,
    StrategyRun,
    StrategyRunCreate,
    StrategyRunPage,
    StrategyRunResponse,
    StrategyRunStatus,
    StrategyRunSummary,
)
from app.repositories.interfaces import (
    Persistence,
    RepositoryConflictError,
)
from app.services.background_jobs import (
    BackgroundJobQueue,
    BackgroundJobService,
    JobExecutionContext,
    JobExecutionFailure,
)
from app.services.intelligence import IntelligenceSearchService

logger = get_logger(__name__)


@dataclass(frozen=True)
class InitialStrategyBundle:
    context: StrategicAgentContext
    evidence: list[IntelligenceEvidence]
    source_coverage: list[str]
    truncated: bool


class StrategyWorkflowState(TypedDict):
    job: BackgroundJob
    job_context: JobExecutionContext
    run: StrategyRun
    bundle: NotRequired[InitialStrategyBundle]
    agent_result: NotRequired[StrategicAgentResult]
    report: NotRequired[StrategicReport]


class StrategyService:
    def __init__(
        self,
        *,
        persistence: Persistence,
        queue: BackgroundJobQueue,
        background_jobs: BackgroundJobService,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._queue = queue
        self._background_jobs = background_jobs
        self._settings = settings

    async def create_run(
        self, context: AuthenticatedContext, request: StrategyRunCreate
    ) -> StrategyRunResponse:
        now = datetime.now(UTC)
        company = await self._persistence.company_profiles.get_for_organization(
            context.organization_id
        )
        if company is None:
            raise AppError(
                code="COMPANY_PROFILE_NOT_FOUND",
                message="Complete the company profile before running strategic analysis.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        query = request.query
        if len(query) > self._settings.strategic_agent_max_query_characters:
            raise AppError(
                code="STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                message="The strategic question is too long.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        competitors = await self._selected_competitors(
            context.organization_id,
            company.id,
            request.competitor_ids,
        )
        if request.mode == StrategyMode.COMPARE and len(competitors) < 2:
            raise AppError(
                code="STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                message="Compare mode requires at least two approved competitors.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        selected_ids = [item.id for item in competitors]
        active_run = await self._persistence.strategy_runs.get_active_for_organization(
            context.organization_id
        )
        if active_run is not None:
            same_explicit_dates = (request.date_from is None and request.date_to is None) or (
                request.date_from == active_run.date_from and request.date_to == active_run.date_to
            )
            if (
                active_run.mode == request.mode
                and active_run.query == query
                and active_run.competitor_ids == selected_ids
                and same_explicit_dates
            ):
                return StrategyRunResponse(run=StrategyRunSummary.from_run(active_run))
            raise AppError(
                code="STRATEGIC_AGENT_RUN_ALREADY_ACTIVE",
                message="This workspace already has an active strategic analysis.",
                status_code=status.HTTP_409_CONFLICT,
            )
        date_to = request.date_to or now
        date_from = request.date_from or (
            date_to - timedelta(days=self._settings.strategic_agent_default_lookback_days)
        )
        if date_from.tzinfo is None or date_to.tzinfo is None:
            raise AppError(
                code="STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                message="Strategic date boundaries must include a timezone.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        date_from = date_from.astimezone(UTC)
        date_to = date_to.astimezone(UTC)
        if date_to > now:
            date_to = now
        if (
            date_from > date_to
            or (date_to - date_from).days > self._settings.strategic_agent_max_lookback_days
        ):
            raise AppError(
                code="STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                message="The strategic date range is invalid or too large.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        _, _, latest_evidence_at = await self._persistence.intelligence_evidence.count_status(
            context.organization_id
        )
        watermark = latest_evidence_at.isoformat() if latest_evidence_at else "no-evidence"
        request_hash = self._request_hash(
            organization_id=context.organization_id,
            company_profile_id=company.id,
            mode=request.mode,
            query=query,
            competitor_ids=selected_ids,
            date_from=date_from,
            date_to=date_to,
            evidence_watermark=watermark,
        )
        duplicate = await self._persistence.strategy_runs.get_active_by_request_hash(
            context.organization_id, request_hash
        )
        if duplicate is not None:
            return StrategyRunResponse(run=StrategyRunSummary.from_run(duplicate))
        if (
            await self._persistence.strategy_runs.count_active()
            >= self._settings.strategic_agent_max_concurrent_runs_global
        ):
            raise AppError(
                code="STRATEGIC_AGENT_RUN_ALREADY_ACTIVE",
                message="Strategic analysis capacity is temporarily full.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        run = StrategyRun(
            id=new_id(),
            organization_id=context.organization_id,
            company_profile_id=company.id,
            requested_by_user_id=context.user_id,
            mode=request.mode,
            query=query,
            competitor_ids=selected_ids,
            date_from=date_from,
            date_to=date_to,
            status=StrategyRunStatus.QUEUED,
            stage="queued",
            progress_percent=0,
            message="Strategic analysis queued.",
            request_hash=request_hash,
            evidence_watermark=watermark,
            analysis_as_of=now,
            agent_version=self._settings.strategic_agent_version,
            prompt_version=self._settings.strategic_agent_prompt_version,
            tool_schema_version=self._settings.strategic_agent_tool_schema_version,
            model_provider=self._settings.strategic_agent_provider,
            model_name=self._settings.strategic_agent_model or "disabled",
            created_at=now,
            updated_at=now,
        )
        try:
            await self._persistence.strategy_runs.create(run)
        except RepositoryConflictError:
            duplicate = await self._persistence.strategy_runs.get_active_by_request_hash(
                context.organization_id, request_hash
            )
            if duplicate:
                return StrategyRunResponse(run=StrategyRunSummary.from_run(duplicate))
            raise
        job = await self._queue.enqueue(
            organization_id=context.organization_id,
            company_profile_id=company.id,
            job_type=JobType.STRATEGIC_ANALYSIS,
            domain_resource_type="strategy_run",
            domain_resource_id=run.id,
            deduplication_key=f"strategic-analysis:{run.id}",
            payload={"strategy_run_id": run.id},
            created_by_user_id=context.user_id,
        )
        run = (
            await self._persistence.strategy_runs.update(
                context.organization_id,
                run.id,
                {"background_job_id": job.id},
            )
            or run
        )
        return StrategyRunResponse(run=StrategyRunSummary.from_run(run))

    async def list_runs(
        self,
        context: AuthenticatedContext,
        *,
        run_status: StrategyRunStatus | None,
        cursor: str | None,
        limit: int,
    ) -> StrategyRunPage:
        items = await self._persistence.strategy_runs.list(
            context.organization_id,
            status=run_status,
            cursor=cursor,
            limit=limit + 1,
        )
        return StrategyRunPage(
            items=[StrategyRunSummary.from_run(item) for item in items[:limit]],
            next_cursor=items[limit - 1].id if len(items) > limit else None,
        )

    async def get_run(self, context: AuthenticatedContext, run_id: str) -> StrategyRunResponse:
        run = await self._persistence.strategy_runs.get_by_id(context.organization_id, run_id)
        if run is None:
            raise self._run_not_found()
        return StrategyRunResponse(run=StrategyRunSummary.from_run(run))

    async def cancel_run(self, context: AuthenticatedContext, run_id: str) -> StrategyRunResponse:
        run = await self._persistence.strategy_runs.get_by_id(context.organization_id, run_id)
        if run is None:
            raise self._run_not_found()
        if run.status not in ACTIVE_STRATEGY_RUN_STATUSES or not run.background_job_id:
            raise AppError(
                code="STRATEGIC_AGENT_RUN_CANCELLED",
                message="This strategic run can no longer be cancelled.",
                status_code=status.HTTP_409_CONFLICT,
            )
        background_job = await self._background_jobs.cancel(context, run.background_job_id)
        immediately_cancelled = background_job.status == BackgroundJobStatus.CANCELLED
        updated = await self._persistence.strategy_runs.update(
            context.organization_id,
            run.id,
            {
                "status": (
                    StrategyRunStatus.CANCELLED
                    if immediately_cancelled
                    else StrategyRunStatus.CANCEL_REQUESTED
                ),
                "stage": "cancelled" if immediately_cancelled else "cancel_requested",
                "message": (
                    "Strategic analysis cancelled."
                    if immediately_cancelled
                    else "Cancellation requested."
                ),
                **(
                    {
                        "cancelled_at": datetime.now(UTC),
                        "completed_at": datetime.now(UTC),
                    }
                    if immediately_cancelled
                    else {}
                ),
            },
        )
        return StrategyRunResponse(run=StrategyRunSummary.from_run(updated or run))

    async def retry_run(self, context: AuthenticatedContext, run_id: str) -> StrategyRunResponse:
        run = await self._persistence.strategy_runs.get_by_id(context.organization_id, run_id)
        if run is None:
            raise self._run_not_found()
        if run.report_id or run.status != StrategyRunStatus.FAILED or not run.background_job_id:
            raise AppError(
                code="STRATEGIC_AGENT_RUN_NOT_RETRYABLE",
                message="This strategic run cannot be retried.",
                status_code=status.HTTP_409_CONFLICT,
            )
        retry = await self._background_jobs.retry(context, run.background_job_id)
        updated = await self._persistence.strategy_runs.update(
            context.organization_id,
            run.id,
            {
                "background_job_id": retry.job.id,
                "status": StrategyRunStatus.QUEUED,
                "stage": "queued",
                "progress_percent": 0,
                "message": "Strategic analysis queued for retry.",
                "error_code": None,
                "error_message": None,
                "completed_at": None,
            },
        )
        return StrategyRunResponse(run=StrategyRunSummary.from_run(updated or run))

    async def list_reports(
        self,
        context: AuthenticatedContext,
        *,
        cursor: str | None,
        limit: int,
    ) -> StrategicReportPage:
        items = await self._persistence.strategy_reports.list(
            context.organization_id, cursor=cursor, limit=limit + 1
        )
        return StrategicReportPage(
            items=[StrategicReportView.from_report(item) for item in items[:limit]],
            next_cursor=items[limit - 1].id if len(items) > limit else None,
        )

    async def get_report(
        self, context: AuthenticatedContext, report_id: str
    ) -> StrategicReportView:
        report = await self._persistence.strategy_reports.get_by_id(
            context.organization_id, report_id
        )
        if report is None:
            raise self._report_not_found()
        return StrategicReportView.from_report(report)

    async def submit_feedback(
        self,
        context: AuthenticatedContext,
        report_id: str,
        request: StrategicReportFeedbackCreate,
    ) -> StrategicReportFeedback:
        report = await self._persistence.strategy_reports.get_by_id(
            context.organization_id, report_id
        )
        if report is None:
            raise self._report_not_found()
        now = datetime.now(UTC)
        return await self._persistence.strategy_report_feedback.upsert(
            StrategicReportFeedback(
                id=new_id(),
                organization_id=context.organization_id,
                report_id=report.id,
                user_id=context.user_id,
                feedback=request.feedback,
                comment=request.comment,
                created_at=now,
                updated_at=now,
            )
        )

    async def citation(
        self,
        context: AuthenticatedContext,
        report_id: str,
        evidence_id: str,
    ) -> EvidenceExcerpt:
        report = await self._persistence.strategy_reports.get_by_id(
            context.organization_id, report_id
        )
        if report is None:
            raise self._report_not_found()
        if evidence_id not in report.evidence_ids:
            raise AppError(
                code="STRATEGIC_AGENT_CITATION_INVALID",
                message="The citation is not part of this strategic report.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        evidence = await self._persistence.intelligence_evidence.get_many(
            context.organization_id, [evidence_id]
        )
        if not evidence or evidence[0].company_profile_id != report.company_profile_id:
            raise AppError(
                code="STRATEGIC_AGENT_CITATION_INVALID",
                message="This supporting evidence is no longer available.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        item = evidence[0]
        competitor = (
            await self._persistence.competitors.get_by_id(
                context.organization_id, item.competitor_id
            )
            if item.competitor_id
            else None
        )
        signal = (
            await self._persistence.competitor_signals.get_by_id(
                context.organization_id, item.signal_id
            )
            if item.signal_id
            else None
        )
        return EvidenceExcerpt(
            evidence_id=item.id,
            competitor_id=item.competitor_id,
            competitor_name=competitor.name if competitor else None,
            source_type=item.source_type,
            source_kind=item.source_kind,
            evidence_kind=item.evidence_kind,
            signal_category=item.category,
            title=item.title,
            section_title=item.section_heading,
            content_excerpt=item.content[:1600],
            source_url=item.source_url,
            observed_at=item.observed_at,
            snapshot_id=item.snapshot_id,
            signal_id=item.signal_id,
            page_start=item.page_number_start,
            page_end=item.page_number_end,
            before_excerpt=signal.before_excerpt if signal else None,
            after_excerpt=signal.after_excerpt if signal else None,
            score=1,
        )

    async def _selected_competitors(
        self,
        organization_id: str,
        company_profile_id: str,
        requested_ids: list[str],
    ) -> list[Competitor]:
        if len(requested_ids) > self._settings.strategic_agent_max_competitors:
            raise AppError(
                code="STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                message="Too many competitors were selected.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if requested_ids:
            items = []
            for competitor_id in requested_ids:
                competitor = await self._persistence.competitors.get_by_id(
                    organization_id, competitor_id
                )
                if (
                    competitor is None
                    or competitor.company_profile_id != company_profile_id
                    or competitor.status != CompetitorStatus.ACTIVE
                ):
                    raise AppError(
                        code="STRATEGIC_AGENT_TOOL_DENIED",
                        message="One or more selected competitors are not approved.",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
                items.append(competitor)
            return items
        return [
            item
            for item in await self._persistence.competitors.list(
                organization_id,
                limit=self._settings.strategic_agent_max_competitors,
            )
            if item.company_profile_id == company_profile_id
            and item.status == CompetitorStatus.ACTIVE
        ]

    def _request_hash(
        self,
        *,
        organization_id: str,
        company_profile_id: str,
        mode: StrategyMode,
        query: str,
        competitor_ids: list[str],
        date_from: datetime,
        date_to: datetime,
        evidence_watermark: str,
    ) -> str:
        payload = {
            "organization_id": organization_id,
            "company_profile_id": company_profile_id,
            "mode": mode.value,
            "query": query.casefold(),
            "competitor_ids": sorted(competitor_ids),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "evidence_watermark": evidence_watermark,
            "agent_version": self._settings.strategic_agent_version,
            "prompt_version": self._settings.strategic_agent_prompt_version,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _run_not_found() -> AppError:
        return AppError(
            code="STRATEGIC_AGENT_RUN_NOT_FOUND",
            message="The strategic run was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @staticmethod
    def _report_not_found() -> AppError:
        return AppError(
            code="STRATEGIC_AGENT_REPORT_NOT_FOUND",
            message="The strategic report was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class StrategicAnalysisRunner:
    FALLBACK_CODES: ClassVar[set[str]] = {
        "STRATEGIC_AGENT_DISABLED",
        "STRATEGIC_AGENT_PROVIDER_UNAVAILABLE",
        "STRATEGIC_AGENT_TIMEOUT",
        "STRATEGIC_AGENT_BUDGET_EXCEEDED",
        "STRATEGIC_AGENT_TOOL_LIMIT_EXCEEDED",
        "STRATEGIC_AGENT_OUTPUT_INVALID",
        "STRATEGIC_AGENT_CITATION_INVALID",
        "STRATEGIC_AGENT_INSUFFICIENT_EVIDENCE",
    }

    def __init__(
        self,
        *,
        persistence: Persistence,
        intelligence: IntelligenceSearchService,
        agent: StrategicAgent,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._intelligence = intelligence
        self._agent = agent
        self._settings = settings
        graph = StateGraph(StrategyWorkflowState)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("analyze", self._analyze)
        graph.add_node("persist", self._persist)
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "analyze")
        graph.add_edge("analyze", "persist")
        graph.add_edge("persist", END)
        self._graph = graph.compile()

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        run = await self._persistence.strategy_runs.get_by_id(
            job.organization_id, job.domain_resource_id
        )
        if run is None:
            raise JobExecutionFailure(
                "STRATEGIC_AGENT_RUN_NOT_FOUND",
                "The strategic run no longer exists.",
                retryable=False,
            )
        existing = await self._persistence.strategy_reports.get_by_run(job.organization_id, run.id)
        if existing is not None:
            await self._persistence.strategy_runs.update(
                run.organization_id,
                run.id,
                {
                    "status": (
                        StrategyRunStatus.PARTIALLY_SUCCEEDED
                        if existing.deterministic_fallback
                        else StrategyRunStatus.SUCCEEDED
                    ),
                    "stage": "completed",
                    "progress_percent": 100,
                    "message": "Strategic report ready.",
                    "report_id": existing.id,
                    "completed_at": existing.created_at,
                },
            )
            return self._result(existing)
        try:
            final = await self._graph.ainvoke(
                {"job": job, "job_context": context, "run": run},
                config={"recursion_limit": 8},
            )
        except StrategicAgentError as exc:
            if exc.code in {"STRATEGIC_AGENT_RUN_CANCELLED", "STRATEGIC_AGENT_LEASE_LOST"}:
                await self._mark_cancelled(run)
                return JobExecutionResult(
                    status=BackgroundJobStatus.CANCELLED,
                    stage="cancelled",
                    message="Strategic analysis cancelled.",
                )
            if exc.retryable and job.attempt_count < job.max_attempts:
                await self._persistence.strategy_runs.update(
                    run.organization_id,
                    run.id,
                    {
                        "status": StrategyRunStatus.QUEUED,
                        "stage": "awaiting_retry",
                        "message": "A temporary provider issue interrupted analysis.",
                        "error_code": exc.code,
                        "error_message": exc.message,
                    },
                )
                raise JobExecutionFailure(exc.code, exc.message, retryable=True) from exc
            await self._mark_failed(run, exc.code, exc.message)
            raise JobExecutionFailure(exc.code, exc.message, retryable=False) from exc
        report = final.get("report")
        if report is None:
            raise JobExecutionFailure(
                "STRATEGIC_AGENT_OUTPUT_INVALID",
                "The strategic workflow did not produce a report.",
                retryable=False,
            )
        return self._result(report)

    async def _retrieve(self, state: StrategyWorkflowState) -> dict[str, object]:
        run = state["run"]
        job = state["job"]
        context = state["job_context"]
        await self._ensure_active(run, job, context)
        await self._progress(
            run,
            job,
            context,
            15,
            StrategyRunStatus.RETRIEVING_EVIDENCE,
            "preparing_company_context",
            "Preparing company context.",
        )
        competitors = []
        for competitor_id in run.competitor_ids:
            competitor = await self._persistence.competitors.get_by_id(
                run.organization_id, competitor_id
            )
            if (
                competitor is None
                or competitor.company_profile_id != run.company_profile_id
                or competitor.status != CompetitorStatus.ACTIVE
            ):
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_TOOL_DENIED",
                    "A selected competitor is no longer approved.",
                )
            competitors.append(competitor)
        evidence = await self._persistence.intelligence_evidence.list_active(
            run.organization_id,
            competitor_ids=run.competitor_ids,
            observed_after=run.date_from,
            observed_before=min(run.date_to, run.analysis_as_of),
            limit=self._settings.strategic_agent_max_evidence_items + 1,
        )
        truncated = len(evidence) > self._settings.strategic_agent_max_evidence_items
        evidence = evidence[: self._settings.strategic_agent_max_evidence_items]
        company = await self._persistence.company_profiles.get_by_id(
            run.organization_id, run.company_profile_id
        )
        if company is None:
            raise StrategicAgentError(
                "COMPANY_PROFILE_NOT_FOUND",
                "The company profile no longer exists.",
            )
        intelligence_profile = (
            await self._persistence.company_intelligence_profiles.get_for_company(
                run.organization_id, run.company_profile_id
            )
        )
        auth = AuthenticatedContext(
            user_id=run.requested_by_user_id,
            organization_id=run.organization_id,
        )
        trends = await self._intelligence.trends(
            auth,
            window_days=min(
                self._settings.strategic_agent_default_lookback_days,
                self._settings.strategic_agent_max_lookback_days,
            ),
            observed_before=min(run.date_to, run.analysis_as_of),
            competitor_ids=run.competitor_ids,
        )
        correlations = await self._intelligence.correlations(
            auth,
            competitor_ids=run.competitor_ids,
            observed_after=run.date_from,
            observed_before=min(run.date_to, run.analysis_as_of),
        )
        source_ids = {f"company-profile:{company.id}"}
        if intelligence_profile:
            source_ids.add(f"company-intelligence:{intelligence_profile.id}")
        initial: dict[str, Any] = {
            "company_context": {
                "company_name": company.company_name,
                "one_line_description": company.one_line_description,
                "industry": company.industry,
                "business_stage": company.business_stage,
                "target_customer_segments": company.target_customer_segments,
                "product_name": company.product_name,
                "product_categories": company.product_categories,
                "primary_use_cases": company.primary_use_cases,
                "differentiators": company.differentiators,
                "strategic_goals": company.strategic_goals,
                "source_ids": sorted(source_ids),
            },
            "competitors": [
                {
                    "competitor_id": item.id,
                    "name": item.name,
                    "description": item.description,
                    "relationship_type": item.relationship_type,
                    "last_successful_scan_at": item.last_successful_scan_at,
                }
                for item in competitors
            ],
            "evidence": [
                {
                    "evidence_id": item.id,
                    "competitor_id": item.competitor_id,
                    "source_type": item.source_type,
                    "category": item.category,
                    "title": item.title,
                    "content_excerpt": item.content[:1400],
                    "observed_at": item.observed_at,
                }
                for item in evidence
            ],
            "deterministic_trends": [item.model_dump(mode="json") for item in trends[:20]],
            "deterministic_correlations": [item.model_dump(mode="json") for item in correlations][
                :20
            ],
        }
        initial_json = json.dumps(initial, default=str, ensure_ascii=True)
        while (
            len(initial_json) > self._settings.strategic_agent_max_context_characters
            and initial["evidence"]
        ):
            initial["evidence"].pop()
            evidence.pop()
            truncated = True
            initial_json = json.dumps(initial, default=str, ensure_ascii=True)
        agent_context = StrategicAgentContext(
            strategy_run_id=run.id,
            organization_id=run.organization_id,
            company_profile_id=run.company_profile_id,
            requested_by_user_id=run.requested_by_user_id,
            analysis_as_of=run.analysis_as_of,
            date_from=run.date_from,
            date_to=run.date_to,
            allowed_competitor_ids=frozenset(run.competitor_ids),
            allowed_evidence_ids=frozenset(item.id for item in evidence),
            company_context_source_ids=frozenset(source_ids),
            initial_context=initial_json,
        )
        await self._persistence.strategy_runs.update(
            run.organization_id,
            run.id,
            {
                "initial_evidence_ids": [item.id for item in evidence],
                "retrieved_evidence_count": len(evidence),
                "evidence_version_keys": {item.id: item.version_key for item in evidence},
            },
        )
        return {
            "bundle": InitialStrategyBundle(
                context=agent_context,
                evidence=evidence,
                source_coverage=sorted({item.source_type.value for item in evidence}),
                truncated=truncated,
            )
        }

    async def _analyze(self, state: StrategyWorkflowState) -> dict[str, object]:
        run = state["run"]
        job = state["job"]
        context = state["job_context"]
        bundle = state["bundle"]
        await self._ensure_active(run, job, context)
        await self._progress(
            run,
            job,
            context,
            45,
            StrategyRunStatus.ANALYZING,
            "analyzing",
            "Analyzing product, pricing, positioning, and risk signals.",
        )
        if not bundle.evidence:
            result = StrategicAgentResult(
                report=deterministic_fallback_report(
                    evidence=[],
                    reason="No active evidence was available in the selected range.",
                    competitor_count=len(run.competitor_ids),
                    truncated=False,
                ),
                warnings=["STRATEGIC_AGENT_INSUFFICIENT_EVIDENCE"],
                deterministic_fallback=True,
            )
            return {"agent_result": result}

        async def active_check() -> bool:
            try:
                await self._ensure_active(run, job, context)
            except StrategicAgentError:
                return False
            return True

        try:
            result = await self._agent.analyze(
                request=StrategicAgentRequest(
                    strategy_run_id=run.id,
                    mode=run.mode,
                    query=run.query,
                    competitor_ids=run.competitor_ids,
                    date_from=run.date_from,
                    date_to=run.date_to,
                ),
                context=bundle.context,
                active_check=active_check,
            )
            await validate_strategic_report(
                report=result.report,
                context=bundle.context,
                additional_evidence_ids=result.additional_evidence_ids,
                persistence=self._persistence,
            )
            return {"agent_result": result}
        except StrategicAgentError as exc:
            if exc.retryable and job.attempt_count < job.max_attempts:
                raise
            if exc.code not in self.FALLBACK_CODES:
                raise
            result = StrategicAgentResult(
                report=deterministic_fallback_report(
                    evidence=bundle.evidence,
                    reason=f"Validated strategic synthesis was unavailable ({exc.code}).",
                    competitor_count=len(run.competitor_ids),
                    truncated=bundle.truncated,
                ),
                warnings=[exc.code],
                deterministic_fallback=True,
            )
            return {"agent_result": result}

    async def _persist(self, state: StrategyWorkflowState) -> dict[str, object]:
        run = state["run"]
        job = state["job"]
        context = state["job_context"]
        bundle = state["bundle"]
        result = state["agent_result"]
        await self._ensure_active(run, job, context)
        await self._progress(
            run,
            job,
            context,
            82,
            StrategyRunStatus.VALIDATING,
            "validating_citations",
            "Validating evidence citations.",
        )
        cited_records = await validate_strategic_report(
            report=result.report,
            context=bundle.context,
            additional_evidence_ids=result.additional_evidence_ids,
            persistence=self._persistence,
        )
        await self._ensure_active(run, job, context)
        cited_ids = cited_evidence_ids(result.report)
        coverage = StrategicEvidenceCoverage(
            evidence_count=len(
                set(bundle.context.allowed_evidence_ids) | set(result.additional_evidence_ids)
            ),
            cited_evidence_count=len(cited_ids),
            competitor_count=len(
                {item.competitor_id for item in cited_records if item.competitor_id}
            ),
            source_type_count=len({item.source_type for item in cited_records}),
            earliest_observed_at=min((item.observed_at for item in cited_records), default=None),
            latest_observed_at=max((item.observed_at for item in cited_records), default=None),
            truncated=bundle.truncated,
            warnings=list(
                dict.fromkeys([*result.report.evidence_coverage.warnings, *result.warnings])
            ),
        )
        output = result.report.model_copy(update={"evidence_coverage": coverage})
        now = datetime.now(UTC)
        company_context_ids = sorted(
            {
                source_id
                for recommendation in output.recommendations
                for source_id in recommendation.company_context_source_ids
            }
        )
        report = StrategicReport(
            id=new_id(),
            organization_id=run.organization_id,
            company_profile_id=run.company_profile_id,
            strategy_run_id=run.id,
            mode=run.mode,
            title=output.title,
            executive_summary=output.executive_summary,
            situation_assessment=output.situation_assessment,
            inferences=output.inferences,
            competitor_comparisons=output.competitor_comparisons,
            threats=output.threats,
            opportunities=output.opportunities,
            recommendations=output.recommendations,
            uncertainties=output.uncertainties,
            evidence_gaps=output.evidence_gaps,
            evidence_ids=cited_ids,
            company_context_source_ids=company_context_ids,
            competitor_ids=run.competitor_ids,
            evidence_coverage=coverage,
            source_coverage=sorted({item.source_type.value for item in cited_records}),
            temporal_coverage={
                "from": coverage.earliest_observed_at,
                "to": coverage.latest_observed_at,
            },
            analysis_as_of=run.analysis_as_of,
            date_from=run.date_from,
            date_to=run.date_to,
            agent_version=run.agent_version,
            prompt_version=run.prompt_version,
            model_provider=run.model_provider,
            model_name=run.model_name,
            warnings=list(dict.fromkeys([*run.warnings, *result.warnings])),
            deterministic_fallback=result.deterministic_fallback,
            created_at=now,
            updated_at=now,
        )
        try:
            await self._persistence.strategy_reports.create(report)
        except RepositoryConflictError:
            existing = await self._persistence.strategy_reports.get_by_run(
                run.organization_id, run.id
            )
            if existing is None:
                raise
            report = existing
        final_status = (
            StrategyRunStatus.PARTIALLY_SUCCEEDED
            if report.deterministic_fallback
            else StrategyRunStatus.SUCCEEDED
        )
        updated = await self._persistence.strategy_runs.update(
            run.organization_id,
            run.id,
            {
                "status": final_status,
                "stage": "completed",
                "progress_percent": 100,
                "message": (
                    "Verified evidence summary ready."
                    if report.deterministic_fallback
                    else "Strategic report ready."
                ),
                "report_id": report.id,
                "tool_call_count": result.tool_call_count,
                "subagent_count": result.subagent_count,
                "additional_evidence_ids": result.additional_evidence_ids,
                "retrieved_evidence_count": len(
                    set(bundle.context.allowed_evidence_ids) | set(result.additional_evidence_ids)
                ),
                "cited_evidence_count": len(cited_ids),
                "cited_evidence_ids": cited_ids,
                "warnings": report.warnings,
                "completed_at": now,
            },
        )
        if updated is None:
            raise StrategicAgentError(
                "STRATEGIC_AGENT_LEASE_LOST",
                "The strategic run could not be finalized.",
            )
        await context.report_progress(
            job,
            100,
            "completed",
            "Strategic report ready.",
        )
        return {"report": report}

    async def _progress(
        self,
        run: StrategyRun,
        job: BackgroundJob,
        context: JobExecutionContext,
        percent: int,
        run_status: StrategyRunStatus,
        stage: str,
        message: str,
    ) -> None:
        await self._ensure_active(run, job, context)
        if not await context.report_progress(job, percent, stage, message):
            raise StrategicAgentError(
                "STRATEGIC_AGENT_LEASE_LOST",
                "The strategic worker lease was lost.",
            )
        updated = await self._persistence.strategy_runs.update(
            run.organization_id,
            run.id,
            {
                "status": run_status,
                "stage": stage,
                "progress_percent": percent,
                "message": message,
                **({"started_at": datetime.now(UTC)} if run.started_at is None else {}),
            },
        )
        if updated is None:
            raise StrategicAgentError(
                "STRATEGIC_AGENT_LEASE_LOST",
                "The strategic run could not be updated.",
            )

    async def _ensure_active(
        self,
        run: StrategyRun,
        job: BackgroundJob,
        context: JobExecutionContext,
    ) -> None:
        if await context.cancelled(job):
            code = (
                "STRATEGIC_AGENT_LEASE_LOST"
                if context.lease_lost.is_set()
                else "STRATEGIC_AGENT_RUN_CANCELLED"
            )
            raise StrategicAgentError(
                code,
                "The strategic run is no longer active.",
            )
        current = await self._persistence.strategy_runs.get_by_id(run.organization_id, run.id)
        if current is None or current.status in {
            StrategyRunStatus.CANCEL_REQUESTED,
            StrategyRunStatus.CANCELLED,
        }:
            raise StrategicAgentError(
                "STRATEGIC_AGENT_RUN_CANCELLED",
                "The strategic run was cancelled.",
            )

    async def _mark_cancelled(self, run: StrategyRun) -> None:
        now = datetime.now(UTC)
        await self._persistence.strategy_runs.update(
            run.organization_id,
            run.id,
            {
                "status": StrategyRunStatus.CANCELLED,
                "stage": "cancelled",
                "message": "Strategic analysis cancelled.",
                "error_code": "STRATEGIC_AGENT_RUN_CANCELLED",
                "error_message": "The strategic run was cancelled.",
                "cancelled_at": now,
                "completed_at": now,
            },
        )

    async def _mark_failed(self, run: StrategyRun, code: str, message: str) -> None:
        await self._persistence.strategy_runs.update(
            run.organization_id,
            run.id,
            {
                "status": StrategyRunStatus.FAILED,
                "stage": "failed",
                "message": "Strategic analysis failed.",
                "error_code": code,
                "error_message": message,
                "completed_at": datetime.now(UTC),
            },
        )

    @staticmethod
    def _result(report: StrategicReport) -> JobExecutionResult:
        return JobExecutionResult(
            status=(
                BackgroundJobStatus.PARTIALLY_SUCCEEDED
                if report.deterministic_fallback
                else BackgroundJobStatus.SUCCEEDED
            ),
            stage="completed",
            message="Strategic report ready.",
            result_summary={
                "report_id": report.id,
                "strategy_run_id": report.strategy_run_id,
                "deterministic_fallback": report.deterministic_fallback,
                "citation_count": len(report.evidence_ids),
            },
        )


class StrategicAnalysisJobHandler:
    job_type = JobType.STRATEGIC_ANALYSIS

    def __init__(self, runner: StrategicAnalysisRunner) -> None:
        self._runner = runner

    async def execute(
        self, *, job: BackgroundJob, context: JobExecutionContext
    ) -> JobExecutionResult:
        return await self._runner.execute(job=job, context=context)
