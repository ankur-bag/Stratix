import asyncio
from typing import Protocol

from app.core.logging import get_logger
from app.domain.documents import CompanyDocument, DocumentStatus
from app.domain.intelligence import CompanyIntelligenceProfile, ExtractionStatus
from app.domain.onboarding import OnboardingStatus, new_id, utc_now
from app.graphs.company_initialization import CompanyInitializationGraph
from app.repositories.interfaces import Persistence

logger = get_logger(__name__)


class InitializationRunner(Protocol):
    """Replaceable deterministic company initialization worker."""

    async def run(self, *, job_id: str, organization_id: str) -> None: ...


class DiscoveryStarter(Protocol):
    """Optional boundary for starting discovery after successful initialization."""

    async def start_auto(
        self, *, organization_id: str, company_profile_id: str, requested_by_user_id: str
    ) -> None: ...


class BackgroundExecutor(Protocol):
    """Replaceable boundary for scheduling persisted initialization jobs."""

    def schedule(self, *, job_id: str, organization_id: str) -> None: ...
    async def close(self) -> None: ...


class CompanyInitializationRunner:
    """Execute the typed Phase 2 company intelligence graph."""

    def __init__(
        self,
        persistence: Persistence,
        graph: CompanyInitializationGraph,
        discovery_starter: DiscoveryStarter | None = None,
    ) -> None:
        self._persistence = persistence
        self._graph = graph
        self._discovery_starter = discovery_starter

    async def run(self, *, job_id: str, organization_id: str) -> None:
        job = await self._persistence.initialization_jobs.get_by_id(organization_id, job_id)
        if job is None:
            return
        try:
            result = await self._graph.run(
                job_id=job_id,
                organization_id=organization_id,
                company_profile_id=job.company_profile_id,
            )
            documents = await self._wait_for_documents(organization_id, job.company_profile_id)
            ready_documents = [item for item in documents if item.status == DocumentStatus.READY]
            failed_documents = [item for item in documents if item.status == DocumentStatus.FAILED]
            if result["status"] != "completed" and ready_documents:
                await self._recover_with_documents(
                    organization_id=organization_id,
                    company_profile_id=job.company_profile_id,
                    job_id=job_id,
                    documents=ready_documents,
                )
                result = {**result, "status": "completed"}
            elif result["status"] != "completed":
                error = (
                    result["errors"][0]
                    if result["errors"]
                    else {
                        "code": "INITIALIZATION_FAILED",
                        "message": "Company intelligence initialization could not be completed.",
                    }
                )
                await self._persistence.initialization_jobs.mark_failed(
                    organization_id, job_id, error["code"], error["message"]
                )
                await self._persistence.company_profiles.update_onboarding_status(
                    organization_id,
                    job.company_profile_id,
                    OnboardingStatus.FAILED,
                )
            elif result["status"] == "completed" and ready_documents:
                await self._enrich_with_documents(
                    organization_id,
                    job.company_profile_id,
                    ready_documents,
                )
            elif result["status"] == "completed" and failed_documents:
                company = await self._persistence.company_profiles.get_by_id(
                    organization_id, job.company_profile_id
                )
                if company is not None:
                    metadata = dict(company.workspace_metadata)
                    metadata["document_evidence"] = "failed"
                    metadata["initialization_warning"] = "DOCUMENT_PROCESSING_FAILED"
                    await self._persistence.company_profiles.update_onboarding_status(
                        organization_id,
                        company.id,
                        OnboardingStatus.READY,
                        workspace_metadata=metadata,
                    )
            if result["status"] == "completed" and self._discovery_starter is not None:
                try:
                    await self._discovery_starter.start_auto(
                        organization_id=organization_id,
                        company_profile_id=job.company_profile_id,
                        requested_by_user_id=job.requested_by_user_id,
                    )
                except Exception:
                    # Discovery is an independent job and cannot invalidate a ready company.
                    return
        except Exception as exc:
            logger.error(
                "company_initialization_failed",
                organization_id=organization_id,
                company_profile_id=job.company_profile_id,
                initialization_job_id=job_id,
                exception_type=type(exc).__name__,
            )
            await self._persistence.initialization_jobs.mark_failed(
                organization_id,
                job_id,
                "INITIALIZATION_FAILED",
                "Company workspace initialization could not be completed.",
            )
            await self._persistence.company_profiles.update_onboarding_status(
                organization_id,
                job.company_profile_id,
                OnboardingStatus.FAILED,
            )

    async def _wait_for_documents(
        self, organization_id: str, company_profile_id: str
    ) -> list[CompanyDocument]:
        documents = await self._persistence.company_documents.list_for_organization(organization_id)
        attached = [item for item in documents if item.company_profile_id == company_profile_id]
        for _ in range(450):
            if not any(item.status == DocumentStatus.PROCESSING for item in attached):
                break
            await asyncio.sleep(0.1)
            documents = await self._persistence.company_documents.list_for_organization(
                organization_id
            )
            attached = [item for item in documents if item.company_profile_id == company_profile_id]
        return attached

    async def _recover_with_documents(
        self,
        *,
        organization_id: str,
        company_profile_id: str,
        job_id: str,
        documents: list[CompanyDocument],
    ) -> None:
        company = await self._persistence.company_profiles.get_by_id(
            organization_id, company_profile_id
        )
        if company is None:
            return
        document_ids = [item.id for item in documents]
        chunks = []
        for document in documents:
            if document.active_version is not None:
                chunks.extend(
                    await self._persistence.company_knowledge_chunks.list_for_document_version(
                        organization_id, document.id, document.active_version
                    )
                )
        facts = []
        for chunk in chunks[:20]:
            first_sentence = chunk.text.split(".", 1)[0].strip()
            if 20 <= len(first_sentence) <= 300 and first_sentence not in facts:
                facts.append(first_sentence)
        now = utc_now()
        existing = await self._persistence.company_intelligence_profiles.get_for_company(
            organization_id, company_profile_id
        )
        profile = CompanyIntelligenceProfile(
            id=existing.id if existing else new_id(),
            organization_id=organization_id,
            company_profile_id=company_profile_id,
            company_name_observed=company.company_name,
            tagline=company.one_line_description,
            summary=company.detailed_description or company.one_line_description,
            industries=[company.industry],
            product_categories=company.product_categories,
            products=[company.product_name],
            target_customer_segments=company.target_customer_segments,
            primary_use_cases=company.primary_use_cases,
            key_features=company.key_features,
            differentiators=company.differentiators,
            keywords=company.keywords,
            source_document_ids=document_ids,
            source_document_chunk_ids=[item.id for item in chunks[:200]],
            document_facts=facts,
            provenance={
                "declared": ["company_profile"],
                "document": document_ids,
            },
            extraction_status=ExtractionStatus.PARTIAL,
            extraction_confidence=0.65,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        saved = await self._persistence.company_intelligence_profiles.save(profile)
        await self._persistence.company_profiles.update_onboarding_status(
            organization_id,
            company_profile_id,
            OnboardingStatus.READY,
            workspace_metadata={
                "discovery_state": "ready",
                "intelligence_profile_id": saved.id,
                "website_evidence": "failed",
                "document_evidence": "ready",
                "initialization_warning": "WEBSITE_COLLECTION_FAILED",
            },
        )
        await self._persistence.initialization_jobs.mark_succeeded(organization_id, job_id)

    async def _enrich_with_documents(
        self,
        organization_id: str,
        company_profile_id: str,
        documents: list[CompanyDocument],
    ) -> None:
        profile = await self._persistence.company_intelligence_profiles.get_for_company(
            organization_id, company_profile_id
        )
        if profile is None:
            return
        chunks = []
        for document in documents:
            if document.active_version is not None:
                chunks.extend(
                    await self._persistence.company_knowledge_chunks.list_for_document_version(
                        organization_id, document.id, document.active_version
                    )
                )
        facts: list[str] = []
        for chunk in chunks[:20]:
            first_sentence = chunk.text.split(".", 1)[0].strip()
            if 20 <= len(first_sentence) <= 300 and first_sentence not in facts:
                facts.append(first_sentence)
        document_ids = [item.id for item in documents]
        provenance = dict(profile.provenance)
        provenance["document"] = document_ids
        enriched = profile.model_copy(
            update={
                "source_document_ids": document_ids,
                "source_document_chunk_ids": [item.id for item in chunks[:200]],
                "document_facts": facts,
                "provenance": provenance,
                "updated_at": utc_now(),
            }
        )
        await self._persistence.company_intelligence_profiles.save(enriched)


class InProcessBackgroundExecutor:
    """Schedule non-durable in-process jobs for local Phase 2 execution."""

    def __init__(self, runner: InitializationRunner) -> None:
        self._runner = runner
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, *, job_id: str, organization_id: str) -> None:
        task = asyncio.create_task(self._runner.run(job_id=job_id, organization_id=organization_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
