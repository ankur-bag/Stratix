from typing import Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from app.core.config import Settings
from app.core.logging import get_logger
from app.crawlers.models import CrawlRequest, CrawlResult, WebsiteCrawler
from app.domain.documents import DocumentStatus
from app.domain.intelligence import (
    CompanyIntelligenceProfile,
    ExtractionStatus,
    NormalizedDocument,
    Snapshot,
)
from app.domain.onboarding import (
    CompanyProfile,
    JobStage,
    OnboardingStatus,
    Source,
    new_id,
    utc_now,
)
from app.repositories.interfaces import Persistence, RepositoryUnavailableError
from app.services.extraction import CompanyIntelligenceExtractor
from app.services.normalization import ContentNormalizer

logger = get_logger(__name__)


class InitializationError(TypedDict):
    code: str
    message: str


class CompanyInitializationState(TypedDict):
    organization_id: str
    company_profile_id: str
    initialization_job_id: str
    source_id: str | None
    company_profile: dict[str, object] | None
    source: dict[str, object] | None
    crawl_result: dict[str, object] | None
    normalized_documents: list[dict[str, object]]
    snapshots: list[dict[str, object]]
    snapshot_ids: list[str]
    intelligence_profile: dict[str, object] | None
    intelligence_profile_id: str | None
    crawl_errors: list[dict[str, object]]
    errors: list[InitializationError]
    status: str


class CompanyInitializationGraph:
    """Orchestrate one traceable company intelligence bootstrap."""

    def __init__(
        self,
        *,
        persistence: Persistence,
        crawler: WebsiteCrawler,
        normalizer: ContentNormalizer,
        extractor: CompanyIntelligenceExtractor,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._crawler = crawler
        self._normalizer = normalizer
        self._extractor = extractor
        self._settings = settings
        builder = StateGraph(CompanyInitializationState)
        builder.add_node("mark_job_running", self.mark_job_running)
        builder.add_node("load_company_profile", self.load_company_profile)
        builder.add_node("load_primary_source", self.load_primary_source)
        builder.add_node("crawl_company_website", self.crawl_company_website)
        builder.add_node("normalize_pages", self.normalize_pages)
        builder.add_node("persist_snapshots", self.persist_snapshots)
        builder.add_node("extract_company_intelligence", self.extract_company_intelligence)
        builder.add_node("persist_company_intelligence", self.persist_company_intelligence)
        builder.add_node("mark_company_ready", self.mark_company_ready)
        builder.add_node("finalize_job", self.finalize_job)
        builder.add_node("mark_initialization_failed", self.mark_initialization_failed)
        builder.add_edge(START, "mark_job_running")
        builder.add_edge("mark_job_running", "load_company_profile")
        self._conditional(builder, "load_company_profile", "load_primary_source")
        self._conditional(builder, "load_primary_source", "crawl_company_website")
        self._conditional(builder, "crawl_company_website", "normalize_pages")
        self._conditional(builder, "normalize_pages", "persist_snapshots")
        self._conditional(builder, "persist_snapshots", "extract_company_intelligence")
        self._conditional(builder, "extract_company_intelligence", "persist_company_intelligence")
        self._conditional(builder, "persist_company_intelligence", "mark_company_ready")
        self._conditional(builder, "mark_company_ready", "finalize_job")
        builder.add_edge("finalize_job", END)
        builder.add_edge("mark_initialization_failed", END)
        self._graph = builder.compile()

    @staticmethod
    def _conditional(
        builder: StateGraph[
            CompanyInitializationState,
            None,
            CompanyInitializationState,
            CompanyInitializationState,
        ],
        node: str,
        next_node: str,
    ) -> None:
        builder.add_conditional_edges(
            node,
            CompanyInitializationGraph.route_after_node,
            {"continue": next_node, "fail": "mark_initialization_failed"},
        )

    @staticmethod
    def route_after_node(state: CompanyInitializationState) -> Literal["continue", "fail"]:
        return "fail" if state["errors"] else "continue"

    async def run(
        self, *, job_id: str, organization_id: str, company_profile_id: str
    ) -> CompanyInitializationState:
        initial = CompanyInitializationState(
            organization_id=organization_id,
            company_profile_id=company_profile_id,
            initialization_job_id=job_id,
            source_id=None,
            company_profile=None,
            source=None,
            crawl_result=None,
            normalized_documents=[],
            snapshots=[],
            snapshot_ids=[],
            intelligence_profile=None,
            intelligence_profile_id=None,
            crawl_errors=[],
            errors=[],
            status="queued",
        )
        result = await self._graph.ainvoke(initial)
        return cast(CompanyInitializationState, result)

    async def mark_job_running(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        await self._persistence.initialization_jobs.mark_running(
            state["organization_id"], state["initialization_job_id"]
        )
        return {**state, "status": "running"}

    async def load_company_profile(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        await self._progress(
            state, JobStage.VALIDATING_COMPANY, 10, "Validating company information."
        )
        company = await self._persistence.company_profiles.get_by_id(
            state["organization_id"], state["company_profile_id"]
        )
        if company is None:
            return self._with_error(
                state, "COMPANY_PROFILE_NOT_FOUND", "The company profile was not found."
            )
        await self._persistence.company_profiles.update_onboarding_status(
            state["organization_id"], company.id, OnboardingStatus.INITIALIZING
        )
        return {**state, "company_profile": company.model_dump(mode="json")}

    async def load_primary_source(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        source = await self._persistence.sources.get_primary_for_company(
            state["organization_id"], state["company_profile_id"]
        )
        if source is None:
            return self._with_error(
                state,
                "COMPANY_SOURCE_NOT_FOUND",
                "The primary company website source was not found.",
            )
        return {
            **state,
            "source_id": source.id,
            "source": source.model_dump(mode="json"),
        }

    async def crawl_company_website(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        await self._progress(
            state,
            JobStage.CRAWLING_COMPANY_WEBSITE,
            25,
            "Analyzing the company website.",
        )
        source = self._source(state)
        try:
            result = await self._crawler.crawl(
                CrawlRequest(
                    url=source.url,
                    organization_id=state["organization_id"],
                    company_profile_id=state["company_profile_id"],
                    source_id=source.id,
                    max_depth=self._settings.crawler_max_depth,
                    max_pages=self._settings.crawler_max_pages,
                )
            )
        except Exception as exc:
            logger.error(
                "company_website_crawl_failed",
                organization_id=state["organization_id"],
                company_profile_id=state["company_profile_id"],
                initialization_job_id=state["initialization_job_id"],
                exception_type=type(exc).__name__,
            )
            return self._with_error(
                state, "CRAWL_FAILED", "The company website could not be collected."
            )
        errors = [error.model_dump(mode="json") for error in result.errors]
        updated: CompanyInitializationState = {
            **state,
            "crawl_result": result.model_dump(mode="json"),
            "crawl_errors": errors,
        }
        await self._progress(
            updated,
            JobStage.CRAWLING_COMPANY_WEBSITE,
            35,
            "Analyzing the company website.",
            metrics={"crawl_pages_discovered": len(result.pages)},
        )
        if not result.pages:
            first = result.errors[0] if result.errors else None
            return self._with_error(
                updated,
                first.code.value if first else "CRAWL_NO_USABLE_CONTENT",
                first.message
                if first
                else "The company website produced no usable public content.",
            )
        return updated

    async def normalize_pages(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        await self._progress(state, JobStage.NORMALIZING_CONTENT, 45, "Preparing website content.")
        crawl_result = CrawlResult.model_validate(state["crawl_result"])
        normalized: list[dict[str, object]] = []
        for page in crawl_result.pages:
            document = self._normalizer.normalize(page.markdown, page.visible_text)
            if document.text:
                normalized.append(
                    {
                        "page": page.model_dump(mode="json"),
                        "document": document.model_dump(mode="json"),
                    }
                )
        if not normalized:
            return self._with_error(
                state,
                "CRAWL_NO_USABLE_CONTENT",
                "The company website produced no usable public content.",
            )
        await self._progress(
            state,
            JobStage.NORMALIZING_CONTENT,
            55,
            "Preparing website content.",
            metrics={"crawl_pages_processed": len(normalized)},
        )
        return {**state, "normalized_documents": normalized}

    async def persist_snapshots(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        await self._progress(
            state,
            JobStage.STORING_BASELINE,
            60,
            "Saving the company intelligence baseline.",
        )
        source = self._source(state)
        now = utc_now()
        snapshots: list[Snapshot] = []
        for item in state["normalized_documents"]:
            page = CrawlResult.model_validate(
                {
                    "pages": [item["page"]],
                    "errors": [],
                    "started_at": now,
                    "completed_at": now,
                }
            ).pages[0]
            document = NormalizedDocument.model_validate(item["document"])
            snapshots.append(
                Snapshot(
                    id=new_id(),
                    organization_id=state["organization_id"],
                    company_profile_id=state["company_profile_id"],
                    source_id=source.id,
                    requested_url=page.requested_url,
                    final_url=page.final_url,
                    page_type=self._normalizer.page_type(page.final_url),
                    title=page.title,
                    fetched_at=page.fetched_at,
                    http_status=page.status_code,
                    crawler_used=page.crawler_used,
                    normalized_markdown=document.markdown,
                    normalized_text=document.text,
                    content_hash=document.content_hash,
                    section_hashes=[section.content_hash for section in document.sections],
                    metadata=page.metadata,
                    created_at=now,
                )
            )
        try:
            persisted = await self._persistence.snapshots.create_many(snapshots)
        except RepositoryUnavailableError:
            return self._with_error(
                state,
                "SNAPSHOT_PERSISTENCE_FAILED",
                "The website baseline could not be saved.",
            )
        await self._progress(
            state,
            JobStage.STORING_BASELINE,
            70,
            "Saving the company intelligence baseline.",
            metrics={"snapshot_count": len(persisted)},
        )
        return {
            **state,
            "snapshots": [snapshot.model_dump(mode="json") for snapshot in persisted],
            "snapshot_ids": [snapshot.id for snapshot in persisted],
        }

    async def extract_company_intelligence(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        await self._progress(
            state,
            JobStage.EXTRACTING_COMPANY_CONTEXT,
            75,
            "Building the company intelligence profile.",
        )
        company = CompanyProfile.model_validate(state["company_profile"])
        snapshots = [Snapshot.model_validate(item) for item in state["snapshots"]]
        try:
            profile = await self._extractor.extract(company_profile=company, snapshots=snapshots)
        except Exception:
            return self._with_error(
                state,
                "INTELLIGENCE_EXTRACTION_FAILED",
                "The company intelligence profile could not be built.",
            )
        if state["crawl_errors"]:
            profile = profile.model_copy(update={"extraction_status": ExtractionStatus.PARTIAL})
        return {**state, "intelligence_profile": profile.model_dump(mode="json")}

    async def persist_company_intelligence(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        profile = CompanyIntelligenceProfile.model_validate(state["intelligence_profile"])
        try:
            saved = await self._persistence.company_intelligence_profiles.save(profile)
        except RepositoryUnavailableError:
            return self._with_error(
                state,
                "INTELLIGENCE_EXTRACTION_FAILED",
                "The company intelligence profile could not be saved.",
            )
        return {
            **state,
            "intelligence_profile_id": saved.id,
        }

    async def mark_company_ready(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        await self._progress(
            state,
            JobStage.READY_FOR_DISCOVERY,
            95,
            "The company is ready for competitor discovery.",
        )
        source_id = state["source_id"]
        if source_id is None:
            return self._with_error(
                state,
                "COMPANY_SOURCE_NOT_FOUND",
                "The primary company website source was not found.",
            )
        await self._persistence.sources.mark_ready(state["organization_id"], source_id)
        await self._persistence.company_profiles.update_onboarding_status(
            state["organization_id"],
            state["company_profile_id"],
            OnboardingStatus.READY,
            workspace_metadata={
                "discovery_state": "ready",
                "intelligence_profile_id": state["intelligence_profile_id"] or "",
            },
        )
        return {**state, "status": "ready"}

    async def finalize_job(self, state: CompanyInitializationState) -> CompanyInitializationState:
        await self._persistence.initialization_jobs.mark_succeeded(
            state["organization_id"], state["initialization_job_id"]
        )
        return {**state, "status": "completed"}

    async def mark_initialization_failed(
        self, state: CompanyInitializationState
    ) -> CompanyInitializationState:
        error = (
            state["errors"][0]
            if state["errors"]
            else InitializationError(
                code="INITIALIZATION_FAILED",
                message="Company intelligence initialization could not be completed.",
            )
        )
        documents = await self._persistence.company_documents.list_for_organization(
            state["organization_id"]
        )
        document_evidence_pending = any(
            item.company_profile_id == state["company_profile_id"]
            and item.status in {DocumentStatus.PROCESSING, DocumentStatus.READY}
            for item in documents
        )
        if document_evidence_pending:
            if state["source_id"] is not None and error["code"].startswith("CRAWL_"):
                await self._persistence.sources.mark_failed(
                    state["organization_id"], state["source_id"]
                )
            return {**state, "status": "failed"}
        await self._persistence.initialization_jobs.mark_failed(
            state["organization_id"],
            state["initialization_job_id"],
            error["code"],
            error["message"],
        )
        await self._persistence.company_profiles.update_onboarding_status(
            state["organization_id"],
            state["company_profile_id"],
            OnboardingStatus.FAILED,
        )
        if state["source_id"] is not None and error["code"].startswith("CRAWL_"):
            await self._persistence.sources.mark_failed(
                state["organization_id"], state["source_id"]
            )
        return {**state, "status": "failed"}

    async def _progress(
        self,
        state: CompanyInitializationState,
        stage: JobStage,
        percent: int,
        message: str,
        *,
        metrics: dict[str, int] | None = None,
    ) -> None:
        await self._persistence.initialization_jobs.update_progress(
            state["organization_id"],
            state["initialization_job_id"],
            stage,
            percent,
            message,
            metrics,
        )

    @staticmethod
    def _with_error(
        state: CompanyInitializationState, code: str, message: str
    ) -> CompanyInitializationState:
        return {**state, "errors": [*state["errors"], {"code": code, "message": message}]}

    @staticmethod
    def _source(state: CompanyInitializationState) -> Source:
        return Source.model_validate(state["source"])
