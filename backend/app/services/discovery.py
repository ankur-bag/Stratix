import re
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

import tldextract
from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.domain.discovery import (
    CandidateClassificationBatch,
    CandidatePage,
    CandidateReviewRequest,
    CandidateReviewResponse,
    CandidateReviewResult,
    CandidateStatus,
    Competitor,
    CompetitorCandidate,
    CompetitorStatus,
    DiscoveryEvidence,
    DiscoveryJob,
    DiscoveryJobResponse,
    DiscoveryJobStatus,
    DiscoveryJobSummary,
    DiscoveryStage,
    RelationshipType,
    ReviewDecision,
)
from app.domain.intelligence import CompanyIntelligenceProfile
from app.domain.monitoring import CompetitorSource
from app.domain.onboarding import (
    CompanyProfile,
    OnboardingStatus,
    new_id,
    normalize_website_url,
    utc_now,
)
from app.repositories.interfaces import Persistence, RepositoryConflictError
from app.services.discovery_provider import (
    CompetitorSearchProvider,
    DiscoveryProviderError,
    SearchResult,
)
from app.services.initialization import BackgroundExecutor
from app.services.onboarding import IDEMPOTENCY_KEY_PATTERN

_DOMAIN_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
_NON_COMPANY_DOMAINS = {
    "alternativeto.net",
    "capterra.com",
    "crunchbase.com",
    "facebook.com",
    "g2.com",
    "github.com",
    "linkedin.com",
    "medium.com",
    "reddit.com",
    "wikipedia.org",
    "x.com",
    "youtube.com",
}


@dataclass
class _CandidateGroup:
    name: str
    canonical_url: str
    description: str
    evidence: list[DiscoveryEvidence] = field(default_factory=list)


class CandidateClassifier(Protocol):
    """Optional single-call structured candidate classification boundary."""

    async def classify(
        self, *, candidates: list[CompetitorCandidate]
    ) -> CandidateClassificationBatch: ...


class CompetitorBaselineStarter(Protocol):
    async def start_auto(
        self, *, organization_id: str, competitor_id: str, requested_by_user_id: str
    ) -> None: ...


class MonitoringScheduleStarter(Protocol):
    async def ensure_default(
        self,
        *,
        organization_id: str,
        company_profile_id: str,
        competitor_id: str,
        created_by_user_id: str,
    ) -> object: ...


class DiscoveryClassificationError(Exception):
    """Structured classification referenced unsupported candidate evidence."""


def build_search_queries(
    company: CompanyProfile,
    intelligence: CompanyIntelligenceProfile,
    *,
    maximum: int,
) -> list[str]:
    """Build a stable, bounded set of public-context search queries."""
    observed_name = intelligence.company_name_observed or company.company_name
    industries = [*intelligence.industries, company.industry]
    categories = [*intelligence.product_categories, *company.product_categories]
    use_cases = [*intelligence.primary_use_cases, *company.primary_use_cases]
    segments = [*intelligence.target_customer_segments, *company.target_customer_segments]
    differentiators = [*intelligence.differentiators, *company.differentiators]
    candidates: list[str] = []
    for value in categories:
        candidates.append(f'"{value}" competitors')
    for value in use_cases:
        candidates.append(f'"{value}" software companies')
    candidates.append(f'alternatives to "{observed_name}"')
    for industry in industries:
        for segment in segments[:1]:
            candidates.append(f'"{industry}" startups for "{segment}"')
    for value in differentiators:
        candidates.append(f'"{value}" platform')
    for value in company.known_competitors:
        candidates.append(f'alternatives to "{value}"')
    if intelligence.pricing_model:
        candidates.append(f'"{intelligence.pricing_model}" "{company.industry}" software')
    return _deduplicate(candidates)[:maximum]


def _deduplicate(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())
        marker = normalized.casefold()
        if normalized and marker not in seen:
            seen.add(marker)
            result.append(normalized)
    return result


def canonical_company_domain(url: str) -> tuple[str, str] | None:
    """Return a safe canonical root URL and registrable domain."""
    try:
        safe_url = normalize_website_url(url)
    except ValueError:
        return None
    hostname = (urlsplit(safe_url).hostname or "").removeprefix("www.")
    extracted = _DOMAIN_EXTRACTOR(hostname)
    domain = extracted.top_domain_under_public_suffix or hostname
    if not domain or domain in _NON_COMPANY_DOMAINS:
        return None
    return f"https://{domain}", domain


def normalize_candidates(
    *,
    company: CompanyProfile,
    results: list[tuple[str, SearchResult]],
    job_id: str,
) -> list[CompetitorCandidate]:
    """Normalize, exclude, and merge candidates by registrable domain."""
    own = canonical_company_domain(company.website_url)
    own_domain = own[1] if own else ""
    grouped: dict[str, _CandidateGroup] = {}
    for query, result in results:
        canonical = canonical_company_domain(result.url)
        if canonical is None or canonical[1] == own_domain:
            continue
        root_url, domain = canonical
        evidence = DiscoveryEvidence(
            query=query,
            title=result.title[:300],
            result_url=result.url[:2048],
            snippet=result.snippet[:1000],
            provider=result.provider[:100],
            provider_score=result.score,
        )
        item = grouped.setdefault(
            domain,
            _CandidateGroup(
                name=_candidate_name(result.title, domain),
                canonical_url=root_url,
                description=result.snippet[:500],
            ),
        )
        item.evidence.append(evidence)
        if len(result.snippet) > len(item.description):
            item.description = result.snippet[:500]
    now = utc_now()
    return [
        CompetitorCandidate(
            id=new_id(),
            organization_id=company.organization_id,
            company_profile_id=company.id,
            discovery_job_id=job_id,
            name=item.name,
            canonical_url=item.canonical_url,
            canonical_domain=domain,
            description=item.description,
            relationship_type=RelationshipType.UNKNOWN,
            status=CandidateStatus.PENDING,
            similarity_score=0,
            confidence=0,
            rank=1,
            reasons=["Found in public market search results."],
            evidence=item.evidence,
            source_queries=_deduplicate(e.query for e in item.evidence),
            source_result_urls=_deduplicate(e.result_url for e in item.evidence),
            created_at=now,
            updated_at=now,
        )
        for domain, item in grouped.items()
    ]


def _candidate_name(title: str, domain: str) -> str:
    name = re.split(r"\s+(?:\||\u2013|\u2014|-)\s+", title.strip(), maxsplit=1)[0]
    return (name or domain.split(".")[0]).strip()[:200]


def rank_candidates(
    candidates: list[CompetitorCandidate],
    *,
    company: CompanyProfile,
    intelligence: CompanyIntelligenceProfile,
    settings: Settings,
    query_count: int,
) -> list[CompetitorCandidate]:
    """Apply explainable deterministic overlap and search-evidence scoring."""
    categories = _terms([*company.product_categories, *intelligence.product_categories])
    use_cases = _terms([*company.primary_use_cases, *intelligence.primary_use_cases])
    segments = _terms([*company.target_customer_segments, *intelligence.target_customer_segments])
    keywords = _terms([*company.keywords, *intelligence.keywords])
    known = _terms(company.known_competitors)
    scored: list[CompetitorCandidate] = []
    for candidate in candidates:
        corpus = " ".join(
            [candidate.name, candidate.description, *(e.snippet for e in candidate.evidence)]
        ).casefold()
        matching_categories = _matches(categories, corpus)
        matching_use_cases = _matches(use_cases, corpus)
        matching_segments = _matches(segments, corpus)
        matching_keywords = _matches(keywords, corpus)
        known_match = any(term in corpus or term in candidate.canonical_domain for term in known)
        provider_score = sum(e.provider_score for e in candidate.evidence) / len(candidate.evidence)
        frequency = min(len(candidate.source_queries) / max(query_count, 1), 1.0)
        score = (
            settings.discovery_category_weight * _ratio(matching_categories, categories)
            + settings.discovery_use_case_weight * _ratio(matching_use_cases, use_cases)
            + settings.discovery_segment_weight * _ratio(matching_segments, segments)
            + settings.discovery_keyword_weight * _ratio(matching_keywords, keywords)
            + settings.discovery_frequency_weight * frequency
            + settings.discovery_provider_score_weight * provider_score
            + settings.discovery_known_competitor_weight * float(known_match)
        )
        relationship = (
            RelationshipType.DIRECT
            if matching_categories and matching_use_cases
            else RelationshipType.INDIRECT
            if matching_use_cases or matching_categories
            else RelationshipType.ADJACENT
            if matching_keywords or matching_segments
            else RelationshipType.UNKNOWN
        )
        reasons = []
        if matching_categories:
            reasons.append(f"Matches product categories: {', '.join(matching_categories[:3])}.")
        if matching_use_cases:
            reasons.append(f"Matches use cases: {', '.join(matching_use_cases[:3])}.")
        if known_match:
            reasons.append("Matches a declared known competitor.")
        if len(candidate.source_queries) > 1:
            reasons.append(f"Appeared in {len(candidate.source_queries)} search queries.")
        if not reasons:
            reasons.append("Found in a relevant public market search result.")
        scored.append(
            candidate.model_copy(
                update={
                    "similarity_score": round(min(score, 1.0), 4),
                    "confidence": round(min(0.35 + provider_score * 0.35 + frequency * 0.3, 1), 4),
                    "relationship_type": relationship,
                    "matching_categories": matching_categories,
                    "matching_use_cases": matching_use_cases,
                    "matching_keywords": matching_keywords,
                    "reasons": reasons,
                }
            )
        )
    ordered = sorted(scored, key=lambda item: (-item.similarity_score, item.canonical_domain))
    return [item.model_copy(update={"rank": index}) for index, item in enumerate(ordered, 1)]


def _terms(values: Iterable[str]) -> list[str]:
    return _deduplicate(value.casefold() for value in values if len(value.strip()) >= 2)


def _matches(terms: list[str], corpus: str) -> list[str]:
    return [term for term in terms if term in corpus]


def _ratio(matches: list[str], terms: list[str]) -> float:
    return min(len(matches) / max(min(len(terms), 4), 1), 1.0)


class CompetitorDiscoveryRunner:
    """Execute a bounded, traceable discovery job outside the request path."""

    def __init__(
        self,
        persistence: Persistence,
        provider: CompetitorSearchProvider,
        settings: Settings,
        classifier: CandidateClassifier | None = None,
    ) -> None:
        self._persistence = persistence
        self._provider = provider
        self._settings = settings
        self._classifier = classifier

    async def run(self, *, job_id: str, organization_id: str) -> None:
        job = await self._persistence.discovery_jobs.get_by_id(organization_id, job_id)
        if job is None:
            return
        try:
            await self._persistence.discovery_jobs.mark_running(organization_id, job_id)
            await self._progress(
                job, DiscoveryStage.LOADING_COMPANY_CONTEXT, 10, "Loading company context."
            )
            company = await self._persistence.company_profiles.get_by_id(
                organization_id, job.company_profile_id
            )
            intelligence = await self._persistence.company_intelligence_profiles.get_for_company(
                organization_id, job.company_profile_id
            )
            if company is None or intelligence is None:
                await self._fail(
                    job,
                    "COMPANY_INTELLIGENCE_PROFILE_NOT_FOUND",
                    "Company intelligence is unavailable.",
                )
                return
            await self._progress(
                job, DiscoveryStage.BUILDING_SEARCH_QUERIES, 20, "Building market search queries."
            )
            queries = build_search_queries(
                company, intelligence, maximum=self._settings.discovery_max_queries
            )
            await self._persistence.discovery_jobs.set_queries(organization_id, job_id, queries)
            await self._progress(
                job, DiscoveryStage.SEARCHING_MARKET, 35, "Searching the public market."
            )
            found: list[tuple[str, SearchResult]] = []
            failures = 0
            for query in queries:
                try:
                    results = await self._provider.search(
                        query=query,
                        max_results=self._settings.discovery_max_results_per_query,
                    )
                    found.extend((query, result) for result in results)
                except DiscoveryProviderError:
                    failures += 1
            if not found:
                code = "DISCOVERY_PROVIDER_UNAVAILABLE" if failures else "DISCOVERY_NO_RESULTS"
                await self._fail(job, code, "Competitor discovery produced no usable results.")
                return
            await self._progress(
                job, DiscoveryStage.NORMALIZING_CANDIDATES, 55, "Normalizing candidate evidence."
            )
            candidates = normalize_candidates(company=company, results=found, job_id=job_id)
            if not candidates:
                await self._fail(
                    job, "DISCOVERY_NO_RESULTS", "Competitor discovery produced no usable results."
                )
                return
            await self._progress(
                job, DiscoveryStage.RANKING_CANDIDATES, 70, "Ranking candidate evidence."
            )
            candidates = rank_candidates(
                candidates,
                company=company,
                intelligence=intelligence,
                settings=self._settings,
                query_count=len(queries),
            )
            classification_failed = False
            if self._settings.llm_enabled and self._classifier is not None:
                await self._progress(
                    job,
                    DiscoveryStage.CLASSIFYING_CANDIDATES,
                    78,
                    "Classifying candidate relationships.",
                )
                try:
                    batch = await self._classifier.classify(candidates=candidates)
                    candidates = self._apply_classifications(candidates, batch)
                except (DiscoveryClassificationError, ValueError):
                    classification_failed = True
            await self._progress(
                job, DiscoveryStage.SAVING_CANDIDATES, 88, "Saving candidate evidence."
            )
            await self._persistence.competitor_candidates.create_many(candidates)
            partial = failures > 0 or classification_failed
            await self._persistence.discovery_jobs.mark_awaiting_review(
                organization_id,
                job_id,
                candidate_count=len(candidates),
                provider_error_count=failures,
                partial=partial,
            )
        except Exception:
            await self._fail(
                job,
                "DISCOVERY_PROVIDER_UNAVAILABLE",
                "Competitor discovery could not be completed.",
            )

    async def _progress(
        self, job: DiscoveryJob, stage: DiscoveryStage, percent: int, message: str
    ) -> None:
        await self._persistence.discovery_jobs.update_progress(
            job.organization_id, job.id, stage, percent, message
        )

    async def _fail(self, job: DiscoveryJob, code: str, message: str) -> None:
        await self._persistence.discovery_jobs.mark_failed(
            job.organization_id, job.id, code, message
        )

    @staticmethod
    def _apply_classifications(
        candidates: list[CompetitorCandidate], batch: CandidateClassificationBatch
    ) -> list[CompetitorCandidate]:
        by_domain = {candidate.canonical_domain: candidate for candidate in candidates}
        if any(item.canonical_domain not in by_domain for item in batch.candidates):
            raise DiscoveryClassificationError
        updates = {item.canonical_domain: item for item in batch.candidates}
        return [
            candidate.model_copy(
                update={
                    "name": updates[candidate.canonical_domain].name,
                    "relationship_type": updates[candidate.canonical_domain].relationship_type,
                    "confidence": updates[candidate.canonical_domain].confidence,
                    "reasons": [
                        *candidate.reasons,
                        updates[candidate.canonical_domain].reason,
                    ],
                }
            )
            if candidate.canonical_domain in updates
            else candidate
            for candidate in candidates
        ]


class CompetitorDiscoveryService:
    """Coordinate discovery creation, retrieval, and auditable review."""

    def __init__(
        self,
        persistence: Persistence,
        executor: BackgroundExecutor,
        baseline_starter: CompetitorBaselineStarter | None = None,
        monitoring_schedule_starter: MonitoringScheduleStarter | None = None,
    ) -> None:
        self._persistence = persistence
        self._executor = executor
        self._baseline_starter = baseline_starter
        self._monitoring_schedule_starter = monitoring_schedule_starter

    async def start(
        self, *, context: AuthenticatedContext, idempotency_key: str
    ) -> DiscoveryJobResponse:
        key = idempotency_key.strip()
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
            raise AppError(
                code="INVALID_IDEMPOTENCY_KEY",
                message="A valid Idempotency-Key header is required.",
                status_code=400,
            )
        existing = await self._persistence.discovery_jobs.get_by_idempotency_key(
            context.organization_id, key
        )
        if existing is not None:
            return self._response(existing)
        company = await self._persistence.company_profiles.get_for_organization(
            context.organization_id
        )
        if company is None or company.onboarding_status != OnboardingStatus.READY:
            raise AppError(
                code="COMPANY_NOT_READY",
                message="Company initialization must complete before discovery.",
                status_code=status.HTTP_409_CONFLICT,
            )
        intelligence = await self._persistence.company_intelligence_profiles.get_for_company(
            context.organization_id, company.id
        )
        if intelligence is None:
            raise AppError(
                code="COMPANY_INTELLIGENCE_PROFILE_NOT_FOUND",
                message="The company intelligence profile is not available.",
                status_code=status.HTTP_409_CONFLICT,
            )
        active = await self._persistence.discovery_jobs.get_active_for_company(
            context.organization_id, company.id
        )
        if active is not None:
            raise AppError(
                code="DISCOVERY_ALREADY_RUNNING",
                message="Competitor discovery is already running.",
                status_code=status.HTTP_409_CONFLICT,
            )
        now = utc_now()
        job = DiscoveryJob(
            id=new_id(),
            organization_id=context.organization_id,
            company_profile_id=company.id,
            company_intelligence_profile_id=intelligence.id,
            requested_by_user_id=context.user_id,
            idempotency_key=key,
            status=DiscoveryJobStatus.QUEUED,
            stage=DiscoveryStage.QUEUED,
            progress_percent=0,
            message="Competitor discovery is queued.",
            created_at=now,
            updated_at=now,
        )
        try:
            await self._persistence.discovery_jobs.create(job)
        except RepositoryConflictError:
            existing = await self._persistence.discovery_jobs.get_by_idempotency_key(
                context.organization_id, key
            )
            if existing is None:
                raise AppError(
                    code="DISCOVERY_ALREADY_RUNNING",
                    message="Competitor discovery is already running.",
                    status_code=409,
                ) from None
            return self._response(existing)
        await self._executor.schedule(job_id=job.id, organization_id=context.organization_id)
        return self._response(job)

    async def start_auto(
        self, *, organization_id: str, company_profile_id: str, requested_by_user_id: str
    ) -> None:
        company = await self._persistence.company_profiles.get_by_id(
            organization_id, company_profile_id
        )
        if company is None:
            return
        try:
            await self.start(
                context=AuthenticatedContext(
                    user_id=requested_by_user_id, organization_id=organization_id
                ),
                idempotency_key=f"auto:{company_profile_id}",
            )
        except AppError as exc:
            if exc.code != "DISCOVERY_ALREADY_RUNNING":
                return

    async def get_job(self, context: AuthenticatedContext, job_id: str) -> DiscoveryJob:
        job = await self._persistence.discovery_jobs.get_by_id(context.organization_id, job_id)
        if job is None:
            raise AppError(
                code="DISCOVERY_JOB_NOT_FOUND",
                message="The discovery job was not found.",
                status_code=404,
            )
        return job

    async def get_latest_job(self, context: AuthenticatedContext) -> DiscoveryJob:
        company = await self._persistence.company_profiles.get_for_organization(
            context.organization_id
        )
        if company is None:
            raise AppError(
                code="COMPANY_NOT_READY",
                message="Company initialization must complete before discovery.",
                status_code=409,
            )
        job = await self._persistence.discovery_jobs.get_latest_for_company(
            context.organization_id, company.id
        )
        if job is None:
            raise AppError(
                code="DISCOVERY_JOB_NOT_FOUND",
                message="The discovery job was not found.",
                status_code=404,
            )
        return job

    async def list_candidates(
        self,
        context: AuthenticatedContext,
        *,
        candidate_status: CandidateStatus | None,
        relationship_type: RelationshipType | None,
        minimum_score: float,
        discovery_job_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> CandidatePage:
        items = await self._persistence.competitor_candidates.list(
            context.organization_id,
            candidate_status=candidate_status,
            relationship_type=relationship_type,
            minimum_score=minimum_score,
            discovery_job_id=discovery_job_id,
            cursor=cursor,
            limit=limit + 1,
        )
        return CandidatePage(
            items=items[:limit], next_cursor=items[limit - 1].id if len(items) > limit else None
        )

    async def get_candidate(
        self, context: AuthenticatedContext, candidate_id: str
    ) -> CompetitorCandidate:
        candidate = await self._persistence.competitor_candidates.get_by_id(
            context.organization_id, candidate_id
        )
        if candidate is None:
            raise AppError(
                code="COMPETITOR_CANDIDATE_NOT_FOUND",
                message="The competitor candidate was not found.",
                status_code=404,
            )
        return candidate

    async def review(
        self, context: AuthenticatedContext, request: CandidateReviewRequest
    ) -> CandidateReviewResponse:
        results: list[CandidateReviewResult] = []
        affected_jobs: set[str] = set()
        for decision in request.decisions:
            candidate = await self.get_candidate(context, decision.candidate_id)
            affected_jobs.add(candidate.discovery_job_id)
            if candidate.status != CandidateStatus.PENDING:
                if candidate.status.value != decision.decision.value:
                    raise AppError(
                        code="INVALID_CANDIDATE_DECISION",
                        message="The candidate already has a different review decision.",
                        status_code=409,
                    )
                competitor = await self._persistence.competitors.get_by_candidate(
                    context.organization_id, candidate.id
                )
                results.append(CandidateReviewResult(candidate=candidate, competitor=competitor))
                continue
            competitor = None
            if decision.decision == ReviewDecision.APPROVED:
                competitor = await self._approve(context, candidate)
            updated = await self._persistence.competitor_candidates.mark_reviewed(
                context.organization_id,
                candidate.id,
                CandidateStatus(decision.decision.value),
                context.user_id,
            )
            if updated is None:
                raise AppError(
                    code="COMPETITOR_CANDIDATE_NOT_FOUND",
                    message="The competitor candidate was not found.",
                    status_code=404,
                )
            results.append(CandidateReviewResult(candidate=updated, competitor=competitor))
        for job_id in affected_jobs:
            accepted, rejected = await self._persistence.competitor_candidates.count_reviews(
                context.organization_id, job_id
            )
            await self._persistence.discovery_jobs.update_review_counts(
                context.organization_id, job_id, accepted, rejected
            )
        return CandidateReviewResponse(results=results)

    async def _approve(
        self, context: AuthenticatedContext, candidate: CompetitorCandidate
    ) -> Competitor:
        existing = await self._persistence.competitors.get_by_candidate(
            context.organization_id, candidate.id
        )
        if existing is not None:
            return existing
        duplicate = await self._persistence.competitors.get_by_domain(
            context.organization_id, candidate.canonical_domain
        )
        if duplicate is not None:
            raise AppError(
                code="COMPETITOR_ALREADY_EXISTS",
                message="A competitor with this domain already exists.",
                status_code=409,
            )
        now = utc_now()
        competitor = Competitor(
            id=new_id(),
            organization_id=context.organization_id,
            company_profile_id=candidate.company_profile_id,
            candidate_id=candidate.id,
            name=candidate.name,
            canonical_url=candidate.canonical_url,
            canonical_domain=candidate.canonical_domain,
            description=candidate.description,
            relationship_type=candidate.relationship_type,
            status=CompetitorStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            approved_at=now,
            approved_by_user_id=context.user_id,
            source_count=1,
        )
        source = CompetitorSource(
            id=new_id(),
            organization_id=context.organization_id,
            competitor_id=competitor.id,
            url=competitor.canonical_url,
            created_at=now,
            updated_at=now,
        )
        try:
            await self._persistence.competitors.create(competitor)
            await self._persistence.competitor_sources.create(source)
        except RepositoryConflictError as exc:
            duplicate = await self._persistence.competitors.get_by_domain(
                context.organization_id, candidate.canonical_domain
            )
            if duplicate is not None and duplicate.candidate_id == candidate.id:
                return duplicate
            raise AppError(
                code="COMPETITOR_ALREADY_EXISTS",
                message="A competitor with this domain already exists.",
                status_code=409,
            ) from exc
        if self._monitoring_schedule_starter is not None:
            await self._monitoring_schedule_starter.ensure_default(
                organization_id=context.organization_id,
                company_profile_id=competitor.company_profile_id,
                competitor_id=competitor.id,
                created_by_user_id=context.user_id,
            )
        if self._baseline_starter is not None:
            # Approval must survive independent background scheduling failures.
            with suppress(Exception):
                await self._baseline_starter.start_auto(
                    organization_id=context.organization_id,
                    competitor_id=competitor.id,
                    requested_by_user_id=context.user_id,
                )
            refreshed = await self._persistence.competitors.get_by_id(
                context.organization_id, competitor.id
            )
            if refreshed is not None:
                competitor = refreshed
        return competitor

    async def list_competitors(self, context: AuthenticatedContext) -> list[Competitor]:
        return await self._persistence.competitors.list(context.organization_id, limit=100)

    async def get_competitor(self, context: AuthenticatedContext, competitor_id: str) -> Competitor:
        competitor = await self._persistence.competitors.get_by_id(
            context.organization_id, competitor_id
        )
        if competitor is None:
            raise AppError(
                code="COMPETITOR_NOT_FOUND",
                message="The competitor was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return competitor

    @staticmethod
    def _response(job: DiscoveryJob) -> DiscoveryJobResponse:
        return DiscoveryJobResponse(
            job=DiscoveryJobSummary.model_validate(job, from_attributes=True)
        )
