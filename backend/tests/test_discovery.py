import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_authenticated_context
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.domain.discovery import (
    CandidateClassification,
    CandidateClassificationBatch,
    CandidateStatus,
    CompetitorCandidate,
    DiscoveryJobStatus,
    RelationshipType,
)
from app.domain.intelligence import CompanyIntelligenceProfile, ExtractionStatus
from app.domain.onboarding import CompanyProfile, OnboardingStatus, utc_now
from app.services.discovery import (
    CompetitorDiscoveryRunner,
    CompetitorDiscoveryService,
    build_search_queries,
    normalize_candidates,
    rank_candidates,
)
from app.services.discovery_provider import DiscoveryProviderError, SearchResult
from tests.fakes import InMemoryPersistence, RecordingBackgroundExecutor
from tests.test_onboarding import submit


def intelligence(company: CompanyProfile) -> CompanyIntelligenceProfile:
    now = utc_now()
    return CompanyIntelligenceProfile(
        id="intelligence_1",
        organization_id=company.organization_id,
        company_profile_id=company.id,
        company_name_observed=company.company_name,
        industries=[company.industry],
        product_categories=company.product_categories,
        target_customer_segments=company.target_customer_segments,
        primary_use_cases=company.primary_use_cases,
        differentiators=company.differentiators,
        keywords=company.keywords,
        source_snapshot_ids=["snapshot_1"],
        extraction_status=ExtractionStatus.COMPLETED,
        extraction_confidence=0.8,
        created_at=now,
        updated_at=now,
    )


def seed_ready_company(client: TestClient, persistence: InMemoryPersistence) -> CompanyProfile:
    company_id = submit(client).json()["company_profile"]["id"]
    company = persistence.company_profiles.items[company_id].model_copy(
        update={"onboarding_status": OnboardingStatus.READY}
    )
    persistence.company_profiles.items[company_id] = company
    asyncio.run(persistence.company_intelligence_profiles.save(intelligence(company)))
    return company


class FakeSearchProvider:
    def __init__(
        self,
        results: list[SearchResult],
        *,
        fail_query_number: int | None = None,
    ) -> None:
        self.results = results
        self.fail_query_number = fail_query_number
        self.queries: list[str] = []

    async def search(self, *, query: str, max_results: int) -> list[SearchResult]:
        self.queries.append(query)
        if self.fail_query_number == len(self.queries):
            raise DiscoveryProviderError
        return self.results[:max_results]


def result(
    url: str = "https://rivalone.com/product?utm_source=search",
    *,
    title: str = "Rival One - Voice AI for customer support",
    score: float = 0.8,
) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        snippet="Voice AI developer tools for customer support automation and startups.",
        score=score,
        provider="fake",
    )


def test_discovery_requires_ready_company_and_intelligence(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    submit(client)
    not_ready = client.post(
        "/api/v1/competitor-discovery/jobs",
        headers={"Idempotency-Key": "discovery-key-001"},
    )
    company = next(iter(persistence.company_profiles.items.values())).model_copy(
        update={"onboarding_status": OnboardingStatus.READY}
    )
    persistence.company_profiles.items[company.id] = company
    missing_intelligence = client.post(
        "/api/v1/competitor-discovery/jobs",
        headers={"Idempotency-Key": "discovery-key-002"},
    )

    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["code"] == "COMPANY_NOT_READY"
    assert missing_intelligence.status_code == 409
    assert missing_intelligence.json()["error"]["code"] == "COMPANY_INTELLIGENCE_PROFILE_NOT_FOUND"


def test_discovery_creation_is_idempotent_and_rejects_another_active_job(
    client: TestClient,
    persistence: InMemoryPersistence,
    discovery_background_executor: RecordingBackgroundExecutor,
) -> None:
    seed_ready_company(client, persistence)
    first = client.post(
        "/api/v1/competitor-discovery/jobs",
        headers={"Idempotency-Key": "discovery-key-003"},
    )
    replay = client.post(
        "/api/v1/competitor-discovery/jobs",
        headers={"Idempotency-Key": "discovery-key-003"},
    )
    conflict = client.post(
        "/api/v1/competitor-discovery/jobs",
        headers={"Idempotency-Key": "discovery-key-004"},
    )

    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "DISCOVERY_ALREADY_RUNNING"
    job_id = first.json()["job"]["id"]
    assert discovery_background_executor.scheduled == [(job_id, "org_test")]
    latest = client.get("/api/v1/competitor-discovery/jobs/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == job_id


def test_query_generation_and_candidate_normalization_are_deterministic(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    company = seed_ready_company(client, persistence)
    profile = intelligence(company).model_copy(
        update={"product_categories": ["Voice AI", "voice ai"]}
    )
    first = build_search_queries(company, profile, maximum=5)
    second = build_search_queries(company, profile, maximum=5)
    candidates = normalize_candidates(
        company=company,
        job_id="job_1",
        results=[
            (first[0], result()),
            (first[1], result("https://www.rivalone.com/pricing?ref=other", score=0.6)),
            (first[0], result(company.website_url, title="Own company")),
            (first[0], result("http://127.0.0.1/admin", title="Unsafe")),
            (first[0], result("https://wikipedia.org/wiki/Voice_AI", title="Directory")),
        ],
    )

    assert first == second
    assert len(first) == len({query.casefold() for query in first}) == 5
    assert len(candidates) == 1
    assert candidates[0].canonical_url == "https://rivalone.com"
    assert candidates[0].canonical_domain == "rivalone.com"
    assert len(candidates[0].evidence) == 2
    assert candidates[0].source_queries == first[:2]


def test_ranking_is_deterministic_when_llm_is_disabled(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    company = seed_ready_company(client, persistence)
    profile = intelligence(company)
    queries = build_search_queries(company, profile, maximum=4)
    candidates = normalize_candidates(
        company=company,
        job_id="job_1",
        results=[
            (queries[0], result()),
            (
                queries[0],
                result(
                    "https://unrelated.example.org",
                    title="Unrelated Company",
                    score=0.2,
                ),
            ),
        ],
    )
    settings = Settings(app_env="testing", llm_enabled=False, _env_file=None)

    first = rank_candidates(
        candidates,
        company=company,
        intelligence=profile,
        settings=settings,
        query_count=len(queries),
    )
    second = rank_candidates(
        candidates,
        company=company,
        intelligence=profile,
        settings=settings,
        query_count=len(queries),
    )

    assert first == second
    assert first[0].canonical_domain == "rivalone.com"
    assert first[0].similarity_score > first[1].similarity_score
    assert first[0].rank == 1


def create_discovery_job(
    persistence: InMemoryPersistence, company: CompanyProfile, *, key: str = "runner-key-001"
) -> tuple[CompetitorDiscoveryService, str]:
    executor = RecordingBackgroundExecutor()
    service = CompetitorDiscoveryService(persistence, executor)
    response = asyncio.run(
        service.start(
            context=AuthenticatedContext(user_id="user_test", organization_id="org_test"),
            idempotency_key=key,
        )
    )
    return service, response.job.id


def test_runner_preserves_candidates_after_partial_provider_failure(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    company = seed_ready_company(client, persistence)
    _, job_id = create_discovery_job(persistence, company)
    provider = FakeSearchProvider([result()], fail_query_number=2)
    runner = CompetitorDiscoveryRunner(
        persistence,
        provider,
        Settings(app_env="testing", llm_enabled=False, _env_file=None),
    )

    asyncio.run(runner.run(job_id=job_id, organization_id="org_test"))

    job = persistence.discovery_jobs.items[job_id]
    assert job.status == DiscoveryJobStatus.PARTIALLY_SUCCEEDED
    assert job.provider_error_count == 1
    assert job.candidate_count == 1
    assert len(persistence.competitor_candidates.items) == 1
    assert persistence.company_intelligence_profiles.items


class UnknownReferenceClassifier:
    async def classify(
        self, *, candidates: list[CompetitorCandidate]
    ) -> CandidateClassificationBatch:
        del candidates
        return CandidateClassificationBatch(
            candidates=[
                CandidateClassification(
                    canonical_domain="invented.example",
                    name="Invented",
                    relationship_type=RelationshipType.DIRECT,
                    reason="Unsupported",
                    confidence=0.9,
                )
            ]
        )


def test_invalid_model_references_fall_back_to_baseline_and_mark_partial(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    company = seed_ready_company(client, persistence)
    _, job_id = create_discovery_job(persistence, company)
    runner = CompetitorDiscoveryRunner(
        persistence,
        FakeSearchProvider([result()]),
        Settings(app_env="testing", llm_enabled=True, _env_file=None),
        UnknownReferenceClassifier(),
    )

    asyncio.run(runner.run(job_id=job_id, organization_id="org_test"))

    assert persistence.discovery_jobs.items[job_id].status == DiscoveryJobStatus.PARTIALLY_SUCCEEDED
    candidate = next(iter(persistence.competitor_candidates.items.values()))
    assert candidate.name == "Rival One"
    assert candidate.model_provider is None


def test_no_results_fails_job_without_deleting_intelligence(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    company = seed_ready_company(client, persistence)
    _, job_id = create_discovery_job(persistence, company)
    runner = CompetitorDiscoveryRunner(
        persistence,
        FakeSearchProvider([]),
        Settings(app_env="testing", _env_file=None),
    )

    asyncio.run(runner.run(job_id=job_id, organization_id="org_test"))

    assert persistence.discovery_jobs.items[job_id].status == DiscoveryJobStatus.FAILED
    assert persistence.discovery_jobs.items[job_id].error_code == "DISCOVERY_NO_RESULTS"
    assert persistence.company_intelligence_profiles.items


def completed_candidates(
    client: TestClient, persistence: InMemoryPersistence
) -> tuple[str, list[CompetitorCandidate]]:
    company = seed_ready_company(client, persistence)
    _, job_id = create_discovery_job(persistence, company)
    runner = CompetitorDiscoveryRunner(
        persistence,
        FakeSearchProvider(
            [
                result(),
                result("https://secondrival.com", title="Second Rival - AI platform", score=0.5),
            ]
        ),
        Settings(app_env="testing", _env_file=None),
    )
    asyncio.run(runner.run(job_id=job_id, organization_id="org_test"))
    return job_id, sorted(
        persistence.competitor_candidates.items.values(), key=lambda item: item.rank
    )


def test_candidate_pagination_filtering_and_organization_isolation(
    client: TestClient, persistence: InMemoryPersistence, application: FastAPI
) -> None:
    job_id, candidates = completed_candidates(client, persistence)
    first = client.get(
        "/api/v1/competitor-candidates",
        params={"discovery_job_id": job_id, "limit": 1},
    )
    second = client.get(
        "/api/v1/competitor-candidates",
        params={"discovery_job_id": job_id, "limit": 1, "cursor": first.json()["next_cursor"]},
    )

    async def other_org() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="other", organization_id="other_org")

    application.dependency_overrides[get_authenticated_context] = other_org
    isolated = client.get(f"/api/v1/competitor-candidates/{candidates[0].id}")

    assert first.status_code == second.status_code == 200
    assert len(first.json()["items"]) == len(second.json()["items"]) == 1
    assert isolated.status_code == 404
    assert isolated.json()["error"]["code"] == "COMPETITOR_CANDIDATE_NOT_FOUND"


def test_review_approve_reject_and_replay_are_idempotent(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    job_id, candidates = completed_candidates(client, persistence)
    request: dict[str, Any] = {
        "decisions": [
            {"candidate_id": candidates[0].id, "decision": "approved"},
            {"candidate_id": candidates[1].id, "decision": "rejected"},
        ]
    }
    first = client.post("/api/v1/competitor-candidates/review", json=request)
    replay = client.post("/api/v1/competitor-candidates/review", json=request)
    competitors = client.get("/api/v1/competitors")

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert len(persistence.competitors.items) == 1
    assert len(persistence.competitor_sources.items) == 1
    assert competitors.json()[0]["canonical_domain"] == candidates[0].canonical_domain
    assert persistence.discovery_jobs.items[job_id].accepted_count == 1
    assert persistence.discovery_jobs.items[job_id].rejected_count == 1
    rejected = persistence.competitor_candidates.items[candidates[1].id]
    assert rejected.status == CandidateStatus.REJECTED
    assert rejected.reviewed_by_user_id == "user_test"


def test_rejected_candidate_never_creates_competitor(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    _, candidates = completed_candidates(client, persistence)
    response = client.post(
        "/api/v1/competitor-candidates/review",
        json={"decisions": [{"candidate_id": candidates[0].id, "decision": "rejected"}]},
    )

    assert response.status_code == 200
    assert not persistence.competitors.items
    assert not persistence.competitor_sources.items


def test_approval_rejects_a_duplicate_competitor_domain(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    _, candidates = completed_candidates(client, persistence)
    first = candidates[0]
    approved = client.post(
        "/api/v1/competitor-candidates/review",
        json={"decisions": [{"candidate_id": first.id, "decision": "approved"}]},
    )
    duplicate = first.model_copy(
        update={
            "id": "candidate_duplicate_domain",
            "discovery_job_id": "another_job",
            "status": CandidateStatus.PENDING,
            "reviewed_at": None,
            "reviewed_by_user_id": None,
        }
    )
    asyncio.run(persistence.competitor_candidates.create_many([duplicate]))
    conflict = client.post(
        "/api/v1/competitor-candidates/review",
        json={"decisions": [{"candidate_id": duplicate.id, "decision": "approved"}]},
    )

    assert approved.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "COMPETITOR_ALREADY_EXISTS"
    assert len(persistence.competitors.items) == 1


def test_review_request_rejects_duplicate_candidate_decisions(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    _, candidates = completed_candidates(client, persistence)
    payload = {
        "decisions": [
            {"candidate_id": candidates[0].id, "decision": "approved"},
            {"candidate_id": candidates[0].id, "decision": "rejected"},
        ]
    }

    response = client.post("/api/v1/competitor-candidates/review", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
