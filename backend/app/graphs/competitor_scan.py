import asyncio
import random
from typing import Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.crawlers.models import CrawlRequest, CrawlResult, WebsiteCrawler
from app.domain.discovery import BaselineStatus, Competitor, CompetitorStatus, MonitoringStatus
from app.domain.intelligence import NormalizedDocument
from app.domain.monitoring import CompetitorSource, CompetitorSourceCreate, SourceStatus
from app.domain.onboarding import new_id, utc_now
from app.domain.scanning import (
    ChangeSet,
    CompetitorScanJob,
    CompetitorSnapshot,
    ScanStage,
    ScanStatus,
)
from app.repositories.interfaces import Persistence, RepositoryConflictError
from app.services.change_detection import ChangeDetector
from app.services.competitor_sources import (
    CompetitorSourceDiscoveryService,
    CompetitorSourceService,
)
from app.services.normalization import ContentNormalizer
from app.services.signals import DeterministicSignalBuilder


class ScanError(TypedDict):
    code: str
    message: str
    fatal: bool


class CompetitorScanState(TypedDict):
    organization_id: str
    competitor_id: str
    scan_id: str
    scan_type: str
    rediscover_sources: bool
    competitor: dict[str, object] | None
    source_ids: list[str]
    sources: list[dict[str, object]]
    crawl_results: list[dict[str, object]]
    normalized_documents: list[dict[str, object]]
    previous_snapshots: dict[str, dict[str, object] | None]
    current_snapshots: dict[str, dict[str, object]]
    persisted_snapshot_ids: list[str]
    change_sets: dict[str, dict[str, object]]
    persisted_signal_ids: list[str]
    errors: list[ScanError]
    status: str


class CompetitorScanGraph:
    """Orchestrate bounded competitor collection and deterministic signal creation."""

    def __init__(
        self,
        *,
        persistence: Persistence,
        crawler: WebsiteCrawler,
        normalizer: ContentNormalizer,
        change_detector: ChangeDetector,
        signal_builder: DeterministicSignalBuilder,
        source_discovery: CompetitorSourceDiscoveryService,
        source_service: CompetitorSourceService,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._crawler = crawler
        self._normalizer = normalizer
        self._change_detector = change_detector
        self._signal_builder = signal_builder
        self._source_discovery = source_discovery
        self._source_service = source_service
        self._settings = settings
        builder = StateGraph(CompetitorScanState)
        for name in (
            "mark_scan_running",
            "load_competitor",
            "discover_sources_if_required",
            "load_enabled_sources",
            "collect_sources",
            "normalize_pages",
            "load_previous_snapshots",
            "detect_changes",
            "persist_snapshots",
            "persist_signals",
            "update_competitor_state",
            "finalize_scan",
            "finalize_failed_scan",
        ):
            builder.add_node(name, getattr(self, name))
        builder.add_edge(START, "mark_scan_running")
        builder.add_edge("mark_scan_running", "load_competitor")
        self._conditional(builder, "load_competitor", "discover_sources_if_required")
        self._conditional(builder, "discover_sources_if_required", "load_enabled_sources")
        self._conditional(builder, "load_enabled_sources", "collect_sources")
        self._conditional(builder, "collect_sources", "normalize_pages")
        self._conditional(builder, "normalize_pages", "load_previous_snapshots")
        self._conditional(builder, "load_previous_snapshots", "detect_changes")
        self._conditional(builder, "detect_changes", "persist_snapshots")
        self._conditional(builder, "persist_snapshots", "persist_signals")
        self._conditional(builder, "persist_signals", "update_competitor_state")
        builder.add_edge("update_competitor_state", "finalize_scan")
        builder.add_edge("finalize_scan", END)
        builder.add_edge("finalize_failed_scan", END)
        self._graph = builder.compile()

    @staticmethod
    def _conditional(
        builder: StateGraph[CompetitorScanState, None, CompetitorScanState, CompetitorScanState],
        node: str,
        next_node: str,
    ) -> None:
        builder.add_conditional_edges(
            node,
            CompetitorScanGraph.route_after_node,
            {"continue": next_node, "fail": "finalize_failed_scan"},
        )

    @staticmethod
    def route_after_node(state: CompetitorScanState) -> Literal["continue", "fail"]:
        return "fail" if any(error["fatal"] for error in state["errors"]) else "continue"

    async def run(self, *, scan: CompetitorScanJob) -> CompetitorScanState:
        initial = CompetitorScanState(
            organization_id=scan.organization_id,
            competitor_id=scan.competitor_id,
            scan_id=scan.id,
            scan_type=scan.scan_type.value,
            rediscover_sources=scan.rediscover_sources,
            competitor=None,
            source_ids=[],
            sources=[],
            crawl_results=[],
            normalized_documents=[],
            previous_snapshots={},
            current_snapshots={},
            persisted_snapshot_ids=[],
            change_sets={},
            persisted_signal_ids=[],
            errors=[],
            status="queued",
        )
        result = await self._graph.ainvoke(initial)
        return cast(CompetitorScanState, result)

    async def mark_scan_running(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._persistence.competitor_scans.mark_running(
            state["organization_id"], state["scan_id"]
        )
        return {**state, "status": "running"}

    async def load_competitor(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._progress(
            state, ScanStage.LOADING_COMPETITOR, 5, "Loading competitor monitoring context."
        )
        competitor = await self._persistence.competitors.get_by_id(
            state["organization_id"], state["competitor_id"]
        )
        if competitor is None:
            return self._error(state, "COMPETITOR_NOT_FOUND", "The competitor was not found.")
        if competitor.status == CompetitorStatus.ARCHIVED:
            return self._error(
                state, "COMPETITOR_NOT_ACTIVE", "Archived competitors cannot be scanned."
            )
        return {**state, "competitor": competitor.model_dump(mode="json")}

    async def discover_sources_if_required(self, state: CompetitorScanState) -> CompetitorScanState:
        existing = await self._persistence.competitor_sources.list_for_competitor(
            state["organization_id"], state["competitor_id"], enabled_only=True
        )
        if not state["rediscover_sources"] and existing:
            return state
        await self._progress(
            state, ScanStage.DISCOVERING_SOURCES, 15, "Discovering useful public pages."
        )
        try:
            discovered = await self._source_discovery.discover(
                organization_id=state["organization_id"],
                competitor_id=state["competitor_id"],
            )
            scan = await self._persistence.competitor_scans.get_by_id(
                state["organization_id"], state["scan_id"]
            )
            if scan is None:
                return self._error(
                    state, "COMPETITOR_SCAN_NOT_FOUND", "The scan job was not found."
                )
            context = AuthenticatedContext(
                user_id=scan.requested_by_user_id, organization_id=state["organization_id"]
            )
            for item in discovered:
                try:
                    await self._source_service.create(
                        context,
                        state["competitor_id"],
                        CompetitorSourceCreate(
                            url=item.url,
                            type=item.page_type,
                            priority=item.priority,
                        ),
                        discovered_automatically=True,
                        discovery_method=item.discovery_method,
                        discovery_evidence=item.evidence,
                    )
                except Exception as exc:
                    if getattr(exc, "code", None) != "COMPETITOR_SOURCE_ALREADY_EXISTS":
                        continue
        except Exception:
            if not existing:
                return self._error(
                    state,
                    "COMPETITOR_SOURCE_DISCOVERY_FAILED",
                    "Competitor source discovery could not be completed.",
                )
            return self._error(
                state,
                "COMPETITOR_SOURCE_DISCOVERY_FAILED",
                "Source discovery was incomplete; existing sources will be scanned.",
                fatal=False,
            )
        return state

    async def load_enabled_sources(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._progress(
            state, ScanStage.VALIDATING_SOURCES, 25, "Validating enabled public sources."
        )
        sources = await self._persistence.competitor_sources.list_for_competitor(
            state["organization_id"],
            state["competitor_id"],
            limit=self._settings.competitor_crawler_max_sources,
            enabled_only=True,
        )
        sources = [source for source in sources if source.status != SourceStatus.BLOCKED]
        if not sources:
            return self._error(
                state,
                "COMPETITOR_HAS_NO_ENABLED_SOURCES",
                "The competitor has no enabled public sources.",
            )
        return {
            **state,
            "source_ids": [source.id for source in sources],
            "sources": [source.model_dump(mode="json") for source in sources],
        }

    async def collect_sources(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._progress(
            state,
            ScanStage.COLLECTING_PAGES,
            40,
            "Collecting registered competitor pages.",
            {"source_count": len(state["sources"])},
        )
        results: list[dict[str, object]] = []
        errors = list(state["errors"])
        successful = 0
        for index, raw_source in enumerate(state["sources"]):
            if index and self._settings.competitor_crawler_max_delay_seconds > 0:
                await asyncio.sleep(
                    random.uniform(
                        self._settings.competitor_crawler_min_delay_seconds,
                        self._settings.competitor_crawler_max_delay_seconds,
                    )
                )
            source = CompetitorSource.model_validate(raw_source)
            try:
                result = await self._crawler.crawl(
                    CrawlRequest(
                        url=source.canonical_url,
                        organization_id=state["organization_id"],
                        company_profile_id=state["competitor_id"],
                        source_id=source.id,
                        max_depth=0,
                        max_pages=self._settings.competitor_crawler_max_pages_per_source,
                        max_retries=self._settings.competitor_crawler_max_retries,
                        page_timeout_ms=self._settings.competitor_crawler_page_timeout_ms,
                        max_response_bytes=self._settings.competitor_crawler_max_response_bytes,
                        check_robots=self._settings.competitor_crawler_check_robots,
                    )
                )
            except Exception:
                result = None
            now = utc_now()
            if result is None or not result.pages:
                code = (
                    result.errors[0].code.value
                    if result is not None and result.errors
                    else "COMPETITOR_CRAWL_TIMEOUT"
                )
                status_value = (
                    SourceStatus.BLOCKED
                    if code in {"CRAWL_BLOCKED_BY_ROBOTS", "CRAWL_ACCESS_DENIED"}
                    else SourceStatus.FAILED
                )
                await self._persistence.competitor_sources.update(
                    state["organization_id"],
                    state["competitor_id"],
                    source.id,
                    {
                        "status": status_value,
                        "last_scanned_at": now,
                        "failure_count": source.failure_count + 1,
                        "last_error_code": self._competitor_error(code),
                    },
                )
                errors.append(
                    ScanError(
                        code=self._competitor_error(code),
                        message="A competitor source could not be collected.",
                        fatal=False,
                    )
                )
                continue
            successful += 1
            results.append({"source_id": source.id, "result": result.model_dump(mode="json")})
            await self._persistence.competitor_sources.update(
                state["organization_id"],
                state["competitor_id"],
                source.id,
                {
                    "status": SourceStatus.READY,
                    "last_scanned_at": now,
                    "last_successful_scan_at": now,
                    "failure_count": 0,
                    "last_error_code": None,
                },
            )
        await self._progress(
            state,
            ScanStage.COLLECTING_PAGES,
            48,
            "Registered competitor pages were collected.",
            {
                "successful_source_count": successful,
                "failed_source_count": len(state["sources"]) - successful,
            },
        )
        updated: CompetitorScanState = {
            **state,
            "crawl_results": results,
            "errors": errors,
        }
        if not results:
            return self._error(
                updated,
                "COMPETITOR_CRAWL_NO_USABLE_CONTENT",
                "No competitor source produced usable public content.",
            )
        return updated

    async def normalize_pages(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._progress(
            state, ScanStage.NORMALIZING_CONTENT, 55, "Normalizing collected page content."
        )
        normalized: list[dict[str, object]] = []
        for item in state["crawl_results"]:
            result = CrawlResult.model_validate(item["result"])
            page = result.pages[0]
            document = self._normalizer.normalize(page.markdown, page.visible_text)
            if document.text:
                normalized.append(
                    {
                        "source_id": item["source_id"],
                        "page": page.model_dump(mode="json"),
                        "document": document.model_dump(mode="json"),
                    }
                )
        if not normalized:
            return self._error(
                state,
                "COMPETITOR_CRAWL_NO_USABLE_CONTENT",
                "No competitor source produced usable public content.",
            )
        return {**state, "normalized_documents": normalized}

    async def load_previous_snapshots(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._progress(
            state,
            ScanStage.LOADING_PREVIOUS_SNAPSHOTS,
            65,
            "Loading prior competitor evidence.",
        )
        previous: dict[str, dict[str, object] | None] = {}
        for item in state["normalized_documents"]:
            source_id = str(item["source_id"])
            snapshot = await self._persistence.competitor_snapshots.get_latest_for_source(
                state["organization_id"], source_id
            )
            previous[source_id] = snapshot.model_dump(mode="json") if snapshot else None
        return {**state, "previous_snapshots": previous}

    async def detect_changes(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._progress(
            state, ScanStage.DETECTING_CHANGES, 75, "Comparing verified page versions."
        )
        sources = {
            source.id: source for source in map(CompetitorSource.model_validate, state["sources"])
        }
        current: dict[str, dict[str, object]] = {}
        changes: dict[str, dict[str, object]] = {}
        now = utc_now()
        for item in state["normalized_documents"]:
            source_id = str(item["source_id"])
            source = sources[source_id]
            page = CrawlResult.model_validate(
                {
                    "pages": [item["page"]],
                    "errors": [],
                    "started_at": now,
                    "completed_at": now,
                }
            ).pages[0]
            document = NormalizedDocument.model_validate(item["document"])
            previous_raw = state["previous_snapshots"].get(source_id)
            previous = CompetitorSnapshot.model_validate(previous_raw) if previous_raw else None
            snapshot = CompetitorSnapshot(
                id=new_id(),
                organization_id=state["organization_id"],
                competitor_id=state["competitor_id"],
                source_id=source_id,
                scan_id=state["scan_id"],
                requested_url=source.canonical_url,
                final_url=page.final_url,
                canonical_url=source.canonical_url,
                page_type=source.page_type,
                title=page.title,
                fetched_at=page.fetched_at,
                http_status=page.status_code,
                crawler_used=page.crawler_used,
                normalized_markdown=document.markdown,
                normalized_text=document.text,
                content_hash=document.content_hash,
                section_hashes=[section.content_hash for section in document.sections],
                sections=document.sections,
                metadata=page.metadata,
                previous_snapshot_id=previous.id if previous else None,
                is_baseline=previous is None,
                created_at=now,
            )
            current[source_id] = snapshot.model_dump(mode="json")
            if previous is not None:
                change_set = self._change_detector.compare(previous=previous, current=snapshot)
                changes[source_id] = change_set.model_dump(mode="json")
        return {**state, "current_snapshots": current, "change_sets": changes}

    async def persist_snapshots(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._progress(
            state, ScanStage.SAVING_SNAPSHOTS, 85, "Saving competitor evidence versions."
        )
        persisted: list[str] = []
        current = dict(state["current_snapshots"])
        try:
            for source_id, raw in list(current.items()):
                snapshot = CompetitorSnapshot.model_validate(raw)
                previous_raw = state["previous_snapshots"].get(source_id)
                previous = CompetitorSnapshot.model_validate(previous_raw) if previous_raw else None
                if previous is not None and previous.content_hash == snapshot.content_hash:
                    current.pop(source_id, None)
                    continue
                stored = await self._persistence.competitor_snapshots.create(snapshot)
                persisted.append(stored.id)
        except (RepositoryConflictError, Exception):
            return self._error(
                state,
                "COMPETITOR_SNAPSHOT_PERSISTENCE_FAILED",
                "Competitor evidence could not be saved.",
            )
        await self._progress(
            state,
            ScanStage.SAVING_SNAPSHOTS,
            88,
            "Competitor evidence versions were saved.",
            {"snapshot_count": len(persisted)},
        )
        return {
            **state,
            "current_snapshots": current,
            "persisted_snapshot_ids": persisted,
        }

    async def persist_signals(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._progress(
            state, ScanStage.CREATING_SIGNALS, 92, "Creating objective change records."
        )
        if state["scan_type"] == "baseline":
            await self._progress(
                state,
                ScanStage.CREATING_SIGNALS,
                95,
                "Baseline evidence was established without change signals.",
                {"signal_count": 0},
            )
            return state
        signal_ids: list[str] = []
        errors = list(state["errors"])
        for source_id, raw_change in state["change_sets"].items():
            previous_raw = state["previous_snapshots"].get(source_id)
            current_raw = state["current_snapshots"].get(source_id)
            if previous_raw is None or current_raw is None:
                continue
            previous = CompetitorSnapshot.model_validate(previous_raw)
            current = CompetitorSnapshot.model_validate(current_raw)
            change_set = ChangeSet.model_validate(raw_change)
            signal = self._signal_builder.build(
                previous=previous, current=current, change_set=change_set
            )
            if signal is None:
                continue
            try:
                valid_previous = await self._persistence.competitor_snapshots.get_by_id(
                    state["organization_id"], previous.id
                )
                valid_current = await self._persistence.competitor_snapshots.get_by_id(
                    state["organization_id"], current.id
                )
                if valid_previous is None or valid_current is None:
                    raise ValueError
                stored = await self._persistence.competitor_signals.create(signal)
                signal_ids.append(stored.id)
            except Exception:
                errors.append(
                    ScanError(
                        code="SIGNAL_PERSISTENCE_FAILED",
                        message="A verified change record could not be saved.",
                        fatal=False,
                    )
                )
        await self._progress(
            state,
            ScanStage.CREATING_SIGNALS,
            95,
            "Objective change records were created.",
            {"signal_count": len(signal_ids)},
        )
        return {**state, "persisted_signal_ids": signal_ids, "errors": errors}

    async def update_competitor_state(self, state: CompetitorScanState) -> CompetitorScanState:
        await self._progress(state, ScanStage.FINALIZING, 98, "Finalizing competitor state.")
        now = utc_now()
        partial = bool(state["errors"])
        competitor = Competitor.model_validate(state["competitor"])
        await self._persistence.competitors.update(
            state["organization_id"],
            state["competitor_id"],
            {
                "monitoring_status": MonitoringStatus.ACTIVE,
                "baseline_status": BaselineStatus.PARTIAL if partial else BaselineStatus.READY,
                "last_scan_at": now,
                "last_successful_scan_at": now,
                "latest_scan_job_id": state["scan_id"],
                "source_count": len(state["source_ids"]),
                "signal_count": competitor.signal_count + len(state["persisted_signal_ids"]),
            },
        )
        return state

    async def finalize_scan(self, state: CompetitorScanState) -> CompetitorScanState:
        scan_status = ScanStatus.PARTIALLY_SUCCEEDED if state["errors"] else ScanStatus.SUCCEEDED
        await self._persistence.competitor_scans.finalize(
            state["organization_id"], state["scan_id"], scan_status
        )
        return {**state, "status": scan_status.value}

    async def finalize_failed_scan(self, state: CompetitorScanState) -> CompetitorScanState:
        error = next((item for item in state["errors"] if item["fatal"]), state["errors"][0])
        await self._persistence.competitor_scans.finalize(
            state["organization_id"],
            state["scan_id"],
            ScanStatus.FAILED,
            error_code=error["code"],
            error_message=error["message"],
        )
        competitor = await self._persistence.competitors.get_by_id(
            state["organization_id"], state["competitor_id"]
        )
        if competitor is not None:
            await self._persistence.competitors.update(
                state["organization_id"],
                state["competitor_id"],
                {
                    "monitoring_status": MonitoringStatus.FAILED,
                    "baseline_status": BaselineStatus.FAILED,
                    "last_scan_at": utc_now(),
                    "latest_scan_job_id": state["scan_id"],
                },
            )
        return {**state, "status": "failed"}

    async def _progress(
        self,
        state: CompetitorScanState,
        stage: ScanStage,
        percent: int,
        message: str,
        metrics: dict[str, int] | None = None,
    ) -> None:
        await self._persistence.competitor_scans.update_progress(
            state["organization_id"],
            state["scan_id"],
            stage,
            percent,
            message,
            metrics,
        )

    @staticmethod
    def _error(
        state: CompetitorScanState,
        code: str,
        message: str,
        *,
        fatal: bool = True,
    ) -> CompetitorScanState:
        return {
            **state,
            "errors": [*state["errors"], ScanError(code=code, message=message, fatal=fatal)],
        }

    @staticmethod
    def _competitor_error(code: str) -> str:
        return {
            "CRAWL_BLOCKED_BY_ROBOTS": "COMPETITOR_CRAWL_BLOCKED_BY_ROBOTS",
            "CRAWL_ACCESS_DENIED": "COMPETITOR_CRAWL_ACCESS_DENIED",
            "CRAWL_RATE_LIMITED": "COMPETITOR_CRAWL_RATE_LIMITED",
            "CRAWL_TIMEOUT": "COMPETITOR_CRAWL_TIMEOUT",
            "CRAWL_NO_USABLE_CONTENT": "COMPETITOR_CRAWL_NO_USABLE_CONTENT",
        }.get(code, "COMPETITOR_CRAWL_NO_USABLE_CONTENT")
