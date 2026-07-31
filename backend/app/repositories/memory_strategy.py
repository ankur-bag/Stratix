from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.strategy import (
    ACTIVE_STRATEGY_RUN_STATUSES,
    StrategicReport,
    StrategicReportFeedback,
    StrategyOperationalDiagnostics,
    StrategyRun,
    StrategyRunStatus,
)
from app.repositories.interfaces import RepositoryConflictError


class InMemoryStrategyRunRepository:
    def __init__(self) -> None:
        self.items: dict[str, StrategyRun] = {}

    async def create(self, run: StrategyRun) -> StrategyRun:
        if run.id in self.items:
            raise RepositoryConflictError
        self.items[run.id] = run.model_copy(deep=True)
        return run

    async def get_by_id(self, organization_id: str, run_id: str) -> StrategyRun | None:
        run = self.items.get(run_id)
        return run.model_copy(deep=True) if run and run.organization_id == organization_id else None

    async def get_active_by_request_hash(
        self, organization_id: str, request_hash: str
    ) -> StrategyRun | None:
        return next(
            (
                run.model_copy(deep=True)
                for run in self.items.values()
                if run.organization_id == organization_id
                and run.request_hash == request_hash
                and run.status in ACTIVE_STRATEGY_RUN_STATUSES
            ),
            None,
        )

    async def get_active_for_organization(self, organization_id: str) -> StrategyRun | None:
        active = [
            run
            for run in self.items.values()
            if run.organization_id == organization_id and run.status in ACTIVE_STRATEGY_RUN_STATUSES
        ]
        active.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return active[0].model_copy(deep=True) if active else None

    async def update(
        self, organization_id: str, run_id: str, changes: Mapping[str, Any]
    ) -> StrategyRun | None:
        run = self.items.get(run_id)
        if run is None or run.organization_id != organization_id:
            return None
        updated = run.model_copy(update=dict(changes))
        self.items[run_id] = StrategyRun.model_validate(updated.model_dump())
        return self.items[run_id].model_copy(deep=True)

    async def list(
        self,
        organization_id: str,
        *,
        status: StrategyRunStatus | None,
        cursor: str | None,
        limit: int,
    ) -> list[StrategyRun]:
        items = [
            run
            for run in self.items.values()
            if run.organization_id == organization_id and (status is None or run.status == status)
        ]
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        if cursor:
            index = next((index for index, item in enumerate(items) if item.id == cursor), None)
            if index is not None:
                items = items[index + 1 :]
        return [item.model_copy(deep=True) for item in items[:limit]]

    async def count_active(self, organization_id: str | None = None) -> int:
        return sum(
            run.status in ACTIVE_STRATEGY_RUN_STATUSES
            and (organization_id is None or run.organization_id == organization_id)
            for run in self.items.values()
        )

    async def operational_diagnostics(self) -> StrategyOperationalDiagnostics:
        runs = list(self.items.values())
        queued = [run.created_at for run in runs if run.status == StrategyRunStatus.QUEUED]
        evidence_counts = [run.retrieved_evidence_count for run in runs]
        return StrategyOperationalDiagnostics(
            active_runs=sum(run.status in ACTIVE_STRATEGY_RUN_STATUSES for run in runs),
            oldest_queued_at=min(queued, default=None),
            failed_runs=sum(run.status == StrategyRunStatus.FAILED for run in runs),
            citation_validation_failures=sum(
                run.error_code == "STRATEGIC_AGENT_CITATION_INVALID" for run in runs
            ),
            average_evidence_count=(
                sum(evidence_counts) / len(evidence_counts) if evidence_counts else None
            ),
            budget_exhaustion_count=sum(
                run.error_code
                in {
                    "STRATEGIC_AGENT_BUDGET_EXCEEDED",
                    "STRATEGIC_AGENT_TOOL_LIMIT_EXCEEDED",
                }
                for run in runs
            ),
        )


class InMemoryStrategicReportRepository:
    def __init__(self) -> None:
        self.items: dict[str, StrategicReport] = {}

    async def create(self, report: StrategicReport) -> StrategicReport:
        if report.id in self.items or any(
            item.organization_id == report.organization_id
            and item.strategy_run_id == report.strategy_run_id
            for item in self.items.values()
        ):
            raise RepositoryConflictError
        self.items[report.id] = report.model_copy(deep=True)
        return report

    async def get_by_id(self, organization_id: str, report_id: str) -> StrategicReport | None:
        report = self.items.get(report_id)
        return (
            report.model_copy(deep=True)
            if report and report.organization_id == organization_id
            else None
        )

    async def get_by_run(
        self, organization_id: str, strategy_run_id: str
    ) -> StrategicReport | None:
        return next(
            (
                report.model_copy(deep=True)
                for report in self.items.values()
                if report.organization_id == organization_id
                and report.strategy_run_id == strategy_run_id
            ),
            None,
        )

    async def list(
        self, organization_id: str, *, cursor: str | None, limit: int
    ) -> list[StrategicReport]:
        items = [
            report for report in self.items.values() if report.organization_id == organization_id
        ]
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        if cursor:
            index = next((index for index, item in enumerate(items) if item.id == cursor), None)
            if index is not None:
                items = items[index + 1 :]
        return [item.model_copy(deep=True) for item in items[:limit]]


class InMemoryStrategicReportFeedbackRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str], StrategicReportFeedback] = {}

    async def upsert(self, feedback: StrategicReportFeedback) -> StrategicReportFeedback:
        key = (feedback.organization_id, feedback.report_id, feedback.user_id)
        self.items[key] = feedback.model_copy(deep=True)
        return feedback
