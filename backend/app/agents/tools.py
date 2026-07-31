from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any, cast

from langchain_core.tools import BaseTool, tool

from app.agents.errors import StrategicAgentError
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.discovery import CompetitorStatus
from app.domain.intelligence_evidence import (
    EvidenceSourceType,
    IntelligenceCategory,
    IntelligenceSearchRequest,
)
from app.domain.strategy import StrategicAgentContext
from app.repositories.interfaces import Persistence
from app.services.intelligence import IntelligenceSearchService

logger = get_logger(__name__)

APPROVED_STRATEGIC_TOOL_NAMES = frozenset(
    {
        "get_company_context",
        "get_competitor_profiles",
        "search_intelligence_evidence",
        "get_evidence_details",
        "get_competitor_timeline",
        "get_intelligence_trends",
        "get_intelligence_correlations",
        "compare_competitors",
    }
)

DISABLED_DEEP_AGENT_TOOLS = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"}
)


@dataclass
class StrategicToolRuntime:
    context: StrategicAgentContext
    persistence: Persistence
    intelligence: IntelligenceSearchService
    settings: Settings
    active_check: Callable[[], Awaitable[bool]]
    tool_call_count: int = 0
    output_characters: int = 0
    role_call_counts: dict[str, int] = field(default_factory=dict)
    authorized_evidence_ids: set[str] = field(default_factory=set)
    additional_evidence_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.authorized_evidence_ids.update(self.context.allowed_evidence_ids)

    async def begin(self, tool_name: str, role: str) -> float:
        if tool_name not in APPROVED_STRATEGIC_TOOL_NAMES:
            raise StrategicAgentError(
                "STRATEGIC_AGENT_TOOL_DENIED",
                "The requested strategic tool is not available.",
            )
        if not await self.active_check():
            raise StrategicAgentError(
                "STRATEGIC_AGENT_RUN_CANCELLED",
                "The strategic run is no longer active.",
            )
        if self.tool_call_count >= self.settings.strategic_agent_max_tool_calls:
            raise StrategicAgentError(
                "STRATEGIC_AGENT_TOOL_LIMIT_EXCEEDED",
                "The strategic tool-call budget was exhausted.",
            )
        role_count = self.role_call_counts.get(role, 0)
        if (
            role != "primary"
            and role_count >= self.settings.strategic_agent_max_tool_calls_per_subagent
        ):
            raise StrategicAgentError(
                "STRATEGIC_AGENT_TOOL_LIMIT_EXCEEDED",
                "The specialist tool-call budget was exhausted.",
            )
        self.tool_call_count += 1
        self.role_call_counts[role] = role_count + 1
        return monotonic()

    async def finish(
        self, tool_name: str, role: str, started: float, result: dict[str, Any]
    ) -> dict[str, Any]:
        if not await self.active_check():
            raise StrategicAgentError(
                "STRATEGIC_AGENT_RUN_CANCELLED",
                "The strategic run is no longer active.",
            )
        bounded = self._bound_output(result)
        result_count = len(bounded.get("items", [])) if isinstance(bounded, dict) else 0
        logger.info(
            "strategic_tool_completed",
            strategy_run_id=self.context.strategy_run_id,
            organization_id=self.context.organization_id,
            tool_name=tool_name,
            tool_result_count=result_count,
            tool_duration_ms=round((monotonic() - started) * 1000, 2),
            subagent_role=role,
        )
        return bounded

    def _bound_output(self, result: dict[str, Any]) -> dict[str, Any]:
        clipped = _clip_value(result)
        encoded = json.dumps(clipped, default=str, ensure_ascii=True)
        remaining = self.settings.strategic_agent_max_context_characters - self.output_characters
        if remaining <= 0:
            return {
                "items": [],
                "truncated": True,
                "warnings": ["The strategic context-character budget was exhausted."],
            }
        if len(encoded) > remaining:
            clipped = {
                "items": [],
                "truncated": True,
                "warnings": [
                    "This tool result was omitted because the strategic context limit was reached."
                ],
                "estimated_unreturned_characters": len(encoded),
            }
            encoded = json.dumps(clipped)
        self.output_characters += len(encoded)
        return cast(dict[str, Any], clipped)


class StrategicToolFactory:
    def __init__(
        self,
        persistence: Persistence,
        intelligence: IntelligenceSearchService,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._intelligence = intelligence
        self._settings = settings

    def runtime(
        self,
        context: StrategicAgentContext,
        active_check: Callable[[], Awaitable[bool]],
    ) -> StrategicToolRuntime:
        return StrategicToolRuntime(
            context=context,
            persistence=self._persistence,
            intelligence=self._intelligence,
            settings=self._settings,
            active_check=active_check,
        )

    def build(self, runtime: StrategicToolRuntime, *, role: str) -> list[BaseTool]:
        context = runtime.context
        authenticated = AuthenticatedContext(
            user_id=context.requested_by_user_id,
            organization_id=context.organization_id,
        )

        async def prepare(tool_name: str) -> float:
            return await runtime.begin(tool_name, role)

        async def complete(
            tool_name: str, started: float, result: dict[str, Any]
        ) -> dict[str, Any]:
            return await runtime.finish(tool_name, role, started, result)

        def selected_competitors(values: list[str] | None) -> list[str]:
            selected = list(dict.fromkeys(values or context.allowed_competitor_ids))
            if len(selected) > self._settings.strategic_agent_max_competitors:
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                    "The competitor selection exceeds the configured limit.",
                )
            if any(value not in context.allowed_competitor_ids for value in selected):
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_TOOL_DENIED",
                    "A competitor is outside this strategic run.",
                )
            return selected

        def bounded_dates(date_from: str | None, date_to: str | None) -> tuple[datetime, datetime]:
            start = _parse_datetime(date_from) if date_from else context.date_from
            end = _parse_datetime(date_to) if date_to else context.date_to
            if start < context.date_from or end > min(context.date_to, context.analysis_as_of):
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                    "The requested dates are outside this strategic run.",
                )
            if start > end:
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                    "The requested date range is invalid.",
                )
            return start, end

        @tool
        async def get_company_context() -> dict[str, Any]:
            """Return bounded verified facts for the company in this strategic run."""
            started = await prepare("get_company_context")
            company = await self._persistence.company_profiles.get_by_id(
                context.organization_id, context.company_profile_id
            )
            if company is None:
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_TOOL_DENIED",
                    "The company context is no longer available.",
                )
            intelligence = await self._persistence.company_intelligence_profiles.get_for_company(
                context.organization_id, context.company_profile_id
            )
            sources = [f"company-profile:{company.id}"]
            if intelligence is not None:
                sources.append(f"company-intelligence:{intelligence.id}")
            facts = {
                "company_name": company.company_name,
                "website_url": company.website_url,
                "one_line_description": company.one_line_description,
                "detailed_description": company.detailed_description,
                "industry": company.industry,
                "business_stage": company.business_stage,
                "business_model": company.business_model,
                "target_customer_segments": company.target_customer_segments,
                "product_name": company.product_name,
                "product_categories": company.product_categories,
                "primary_use_cases": company.primary_use_cases,
                "key_features": company.key_features,
                "differentiators": company.differentiators,
                "strategic_goals": company.strategic_goals,
                "source_ids": sources,
                "observed_facts": (
                    intelligence.model_dump(mode="json") if intelligence is not None else None
                ),
            }
            return await complete(
                "get_company_context",
                started,
                {"facts": facts, "truncated": False, "warnings": []},
            )

        @tool
        async def get_competitor_profiles(
            competitor_ids: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            """Return bounded profiles for approved competitors in this run."""
            started = await prepare("get_competitor_profiles")
            ids = selected_competitors(competitor_ids)
            bounded_limit = _bounded_limit(
                limit, min(self._settings.strategic_agent_max_competitors, len(ids) or 1)
            )
            items: list[dict[str, Any]] = []
            for competitor_id in ids[:bounded_limit]:
                competitor = await self._persistence.competitors.get_by_id(
                    context.organization_id, competitor_id
                )
                if (
                    competitor is None
                    or competitor.company_profile_id != context.company_profile_id
                    or competitor.status != CompetitorStatus.ACTIVE
                ):
                    raise StrategicAgentError(
                        "STRATEGIC_AGENT_TOOL_DENIED",
                        "A competitor is no longer approved for this run.",
                    )
                items.append(
                    {
                        "competitor_id": competitor.id,
                        "name": competitor.name,
                        "description": competitor.description,
                        "relationship_type": competitor.relationship_type,
                        "canonical_url": competitor.canonical_url,
                        "monitoring_status": competitor.monitoring_status,
                        "baseline_status": competitor.baseline_status,
                        "last_successful_scan_at": competitor.last_successful_scan_at,
                        "source_count": competitor.source_count,
                        "signal_count": competitor.signal_count,
                    }
                )
            return await complete(
                "get_competitor_profiles",
                started,
                {
                    "items": items,
                    "truncated": len(ids) > len(items),
                    "warnings": (
                        ["Additional selected competitors were omitted."]
                        if len(ids) > len(items)
                        else []
                    ),
                },
            )

        @tool
        async def search_intelligence_evidence(
            query: str,
            competitor_ids: list[str] | None = None,
            source_types: list[str] | None = None,
            signal_categories: list[str] | None = None,
            date_from: str | None = None,
            date_to: str | None = None,
            limit: int = 8,
        ) -> dict[str, Any]:
            """Search Phase 6 evidence within this run's immutable tenant and date scope."""
            started = await prepare("search_intelligence_evidence")
            normalized_query = " ".join(query.split())
            if not 1 < len(normalized_query) <= self._settings.strategic_agent_max_query_characters:
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                    "The strategic evidence query is invalid.",
                )
            ids = selected_competitors(competitor_ids)
            start, end = bounded_dates(date_from, date_to)
            bounded_limit = _bounded_limit(
                limit, self._settings.strategic_agent_max_evidence_per_tool_call
            )
            try:
                request = IntelligenceSearchRequest(
                    query=normalized_query,
                    competitor_ids=ids,
                    source_types=[EvidenceSourceType(value) for value in source_types or []],
                    categories=[IntelligenceCategory(value) for value in signal_categories or []],
                    observed_after=start,
                    observed_before=end,
                    include_company_context=False,
                    limit=bounded_limit,
                )
            except ValueError as exc:
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                    "The strategic evidence filters are invalid.",
                ) from exc
            response = await self._intelligence.search(authenticated, request)
            items = []
            for item in response.bundle.items:
                if item.competitor_id not in context.allowed_competitor_ids:
                    continue
                runtime.authorized_evidence_ids.add(item.evidence_id)
                if item.evidence_id not in context.allowed_evidence_ids:
                    runtime.additional_evidence_ids.add(item.evidence_id)
                items.append(item.model_dump(mode="json"))
            return await complete(
                "search_intelligence_evidence",
                started,
                {
                    "items": items,
                    "coverage": response.bundle.coverage.model_dump(mode="json"),
                    "truncated": len(items) >= bounded_limit,
                    "warnings": response.bundle.warnings,
                },
            )

        @tool
        async def get_evidence_details(evidence_ids: list[str]) -> dict[str, Any]:
            """Return bounded canonical details for evidence already authorized in this run."""
            started = await prepare("get_evidence_details")
            unique_ids = list(dict.fromkeys(evidence_ids))
            maximum = self._settings.strategic_agent_max_evidence_per_tool_call
            if not unique_ids or len(unique_ids) > maximum:
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                    "The evidence ID list is empty or exceeds the configured limit.",
                )
            if any(item not in runtime.authorized_evidence_ids for item in unique_ids):
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_TOOL_DENIED",
                    "Evidence details may only be loaded from this run's authorized results.",
                )
            canonical = await self._persistence.intelligence_evidence.get_many(
                context.organization_id, unique_ids
            )
            valid = [
                item
                for item in canonical
                if item.company_profile_id == context.company_profile_id
                and item.competitor_id in context.allowed_competitor_ids
                and context.date_from
                <= item.observed_at
                <= min(context.date_to, context.analysis_as_of)
            ]
            items = [
                {
                    "evidence_id": item.id,
                    "competitor_id": item.competitor_id,
                    "source_type": item.source_type,
                    "source_kind": item.source_kind,
                    "evidence_kind": item.evidence_kind,
                    "category": item.category,
                    "title": item.title,
                    "section_heading": item.section_heading,
                    "content_excerpt": item.content[:1600],
                    "source_url": item.source_url,
                    "observed_at": item.observed_at,
                    "page_number_start": item.page_number_start,
                    "page_number_end": item.page_number_end,
                    "snapshot_id": item.snapshot_id,
                    "signal_id": item.signal_id,
                    "version_key": item.version_key,
                }
                for item in valid
            ]
            return await complete(
                "get_evidence_details",
                started,
                {
                    "items": items,
                    "truncated": False,
                    "warnings": (
                        ["Some evidence is no longer active or authorized."]
                        if len(items) != len(unique_ids)
                        else []
                    ),
                },
            )

        @tool
        async def get_competitor_timeline(
            competitor_id: str,
            limit: int = 12,
        ) -> dict[str, Any]:
            """Return deterministic Phase 6 timeline events for one selected competitor."""
            started = await prepare("get_competitor_timeline")
            selected_competitors([competitor_id])
            bounded_limit = _bounded_limit(
                limit, self._settings.strategic_agent_max_evidence_per_tool_call
            )
            page = await self._intelligence.timeline(
                authenticated,
                competitor_id,
                cursor=None,
                limit=bounded_limit,
                categories=None,
                source_types=None,
                observed_after=context.date_from,
                observed_before=min(context.date_to, context.analysis_as_of),
                ascending=False,
            )
            for item in page.items:
                runtime.authorized_evidence_ids.add(item.evidence_id)
                if item.evidence_id not in context.allowed_evidence_ids:
                    runtime.additional_evidence_ids.add(item.evidence_id)
            return await complete(
                "get_competitor_timeline",
                started,
                {
                    "items": [item.model_dump(mode="json") for item in page.items],
                    "truncated": page.next_cursor is not None,
                    "warnings": [],
                },
            )

        @tool
        async def get_intelligence_trends(window_days: int = 90) -> dict[str, Any]:
            """Return deterministic Phase 6 trends, never model-created trend labels."""
            started = await prepare("get_intelligence_trends")
            if not 1 <= window_days <= self._settings.strategic_agent_max_lookback_days:
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                    "The trend window is outside the configured range.",
                )
            trends = await self._intelligence.trends(
                authenticated,
                window_days=window_days,
                observed_before=min(context.date_to, context.analysis_as_of),
                competitor_ids=sorted(context.allowed_competitor_ids),
            )
            items = [item.model_dump(mode="json") for item in trends[:20]]
            return await complete(
                "get_intelligence_trends",
                started,
                {
                    "items": items,
                    "truncated": len(trends) > len(items),
                    "warnings": [],
                },
            )

        @tool
        async def get_intelligence_correlations(limit: int = 12) -> dict[str, Any]:
            """Return deterministic Phase 6 cross-source correlations."""
            started = await prepare("get_intelligence_correlations")
            bounded_limit = _bounded_limit(limit, 20)
            correlations = await self._intelligence.correlations(
                authenticated,
                competitor_ids=sorted(context.allowed_competitor_ids),
                observed_after=context.date_from,
                observed_before=min(context.date_to, context.analysis_as_of),
            )
            items = []
            for correlation in correlations:
                runtime.authorized_evidence_ids.update(correlation.evidence_ids)
                runtime.additional_evidence_ids.update(
                    set(correlation.evidence_ids) - set(context.allowed_evidence_ids)
                )
                record = correlation.model_dump(mode="json")
                items.append(record)
                if len(items) >= bounded_limit:
                    break
            return await complete(
                "get_intelligence_correlations",
                started,
                {
                    "items": items,
                    "truncated": len(correlations) > len(items),
                    "warnings": [],
                },
            )

        @tool
        async def compare_competitors(
            competitor_ids: list[str],
            topic: str,
            limit: int = 12,
        ) -> dict[str, Any]:
            """Return selected competitor profiles plus bounded evidence for a comparison topic."""
            started = await prepare("compare_competitors")
            ids = selected_competitors(competitor_ids)
            if len(ids) < 2:
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                    "Comparison requires at least two selected competitors.",
                )
            normalized_topic = " ".join(topic.split())
            if not 1 < len(normalized_topic) <= self._settings.strategic_agent_max_query_characters:
                raise StrategicAgentError(
                    "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
                    "The comparison topic is invalid.",
                )
            profiles = []
            for competitor_id in ids:
                competitor = await self._persistence.competitors.get_by_id(
                    context.organization_id, competitor_id
                )
                if (
                    competitor is None
                    or competitor.company_profile_id != context.company_profile_id
                    or competitor.status != CompetitorStatus.ACTIVE
                ):
                    raise StrategicAgentError(
                        "STRATEGIC_AGENT_TOOL_DENIED",
                        "A competitor is no longer approved for comparison.",
                    )
                profiles.append(
                    {
                        "competitor_id": competitor.id,
                        "name": competitor.name,
                        "description": competitor.description,
                        "relationship_type": competitor.relationship_type,
                    }
                )
            response = await self._intelligence.search(
                authenticated,
                IntelligenceSearchRequest(
                    query=normalized_topic,
                    competitor_ids=ids,
                    observed_after=context.date_from,
                    observed_before=min(context.date_to, context.analysis_as_of),
                    limit=_bounded_limit(
                        limit,
                        self._settings.strategic_agent_max_evidence_per_tool_call,
                    ),
                ),
            )
            evidence = []
            for item in response.bundle.items:
                if item.competitor_id not in context.allowed_competitor_ids:
                    continue
                runtime.authorized_evidence_ids.add(item.evidence_id)
                runtime.additional_evidence_ids.add(item.evidence_id)
                evidence.append(item.model_dump(mode="json"))
            return await complete(
                "compare_competitors",
                started,
                {
                    "profiles": profiles,
                    "items": evidence,
                    "coverage": response.bundle.coverage.model_dump(mode="json"),
                    "truncated": len(evidence)
                    >= self._settings.strategic_agent_max_evidence_per_tool_call,
                    "warnings": response.bundle.warnings,
                },
            )

        tools = [
            get_company_context,
            get_competitor_profiles,
            search_intelligence_evidence,
            get_evidence_details,
            get_competitor_timeline,
            get_intelligence_trends,
            get_intelligence_correlations,
            compare_competitors,
        ]
        assert {item.name for item in tools} == APPROVED_STRATEGIC_TOOL_NAMES
        return tools


def _bounded_limit(value: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise StrategicAgentError(
            "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
            "The requested result limit is invalid.",
        )
    return value


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise StrategicAgentError(
            "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
            "A strategic tool received an invalid date.",
        ) from exc
    if parsed.tzinfo is None:
        raise StrategicAgentError(
            "STRATEGIC_AGENT_INVALID_TOOL_ARGUMENTS",
            "Strategic tool dates must include a timezone.",
        )
    return parsed


def _clip_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 2000 else f"{value[:1997]}..."
    if isinstance(value, list):
        return [_clip_value(item) for item in value[:40]]
    if isinstance(value, dict):
        return {str(key): _clip_value(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    return value
