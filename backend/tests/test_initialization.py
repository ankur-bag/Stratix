import asyncio

from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.crawlers.models import CrawlError, CrawlErrorCode
from app.domain.intelligence import CompanyIntelligenceProfile, Snapshot
from app.domain.onboarding import CompanyOnboardingRequest, JobStage, JobStatus
from app.graphs.company_initialization import CompanyInitializationGraph
from app.services.extraction import (
    CompanyIntelligenceExtractor,
    DeterministicCompanyIntelligenceExtractor,
    IntelligenceExtractionError,
)
from app.services.initialization import CompanyInitializationRunner, DiscoveryStarter
from app.services.normalization import ContentNormalizer
from app.services.onboarding import CompanyOnboardingService
from tests.fakes import (
    FakeWebsiteCrawler,
    InMemoryPersistence,
    RecordingBackgroundExecutor,
    crawl_page,
)
from tests.test_onboarding import valid_payload


def create_submission(
    persistence: InMemoryPersistence,
) -> tuple[str, str]:
    service = CompanyOnboardingService(persistence, RecordingBackgroundExecutor())
    response = asyncio.run(
        service.submit(
            context=AuthenticatedContext(user_id="user_test", organization_id="org_test"),
            idempotency_key="runner-test-key",
            request=CompanyOnboardingRequest.model_validate(valid_payload()),
        )
    )
    return response.company_profile.id, response.initialization_job.id


def runner(
    persistence: InMemoryPersistence,
    *,
    crawler: FakeWebsiteCrawler | None = None,
    extractor: CompanyIntelligenceExtractor | None = None,
    discovery_starter: DiscoveryStarter | None = None,
) -> CompanyInitializationRunner:
    graph = CompanyInitializationGraph(
        persistence=persistence,
        crawler=crawler or FakeWebsiteCrawler(),
        normalizer=ContentNormalizer(),
        extractor=extractor or DeterministicCompanyIntelligenceExtractor(),
        settings=Settings(
            app_env="testing",
            crawler_min_delay_seconds=0,
            crawler_max_delay_seconds=0,
            _env_file=None,
        ),
    )
    return CompanyInitializationRunner(persistence, graph, discovery_starter)


class FailingExtractor:
    async def extract(
        self, *, company_profile: object, snapshots: list[Snapshot]
    ) -> CompanyIntelligenceProfile:
        raise IntelligenceExtractionError


class RecordingDiscoveryStarter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    async def start_auto(
        self, *, organization_id: str, company_profile_id: str, requested_by_user_id: str
    ) -> None:
        self.calls.append((organization_id, company_profile_id, requested_by_user_id))
        if self.fail:
            raise RuntimeError


def test_runner_completes_deterministic_transitions(
    persistence: InMemoryPersistence,
) -> None:
    company_id, job_id = create_submission(persistence)

    asyncio.run(runner(persistence).run(job_id=job_id, organization_id="org_test"))

    job = persistence.initialization_jobs.items[job_id]
    company = persistence.company_profiles.items[company_id]
    source = next(iter(persistence.sources.items.values()))
    assert job.status == JobStatus.SUCCEEDED
    assert job.stage == JobStage.COMPLETED
    assert job.progress_percent == 100
    assert persistence.initialization_jobs.stage_history == [
        JobStage.VALIDATING_COMPANY,
        JobStage.CRAWLING_COMPANY_WEBSITE,
        JobStage.CRAWLING_COMPANY_WEBSITE,
        JobStage.NORMALIZING_CONTENT,
        JobStage.NORMALIZING_CONTENT,
        JobStage.STORING_BASELINE,
        JobStage.STORING_BASELINE,
        JobStage.EXTRACTING_COMPANY_CONTEXT,
        JobStage.READY_FOR_DISCOVERY,
        JobStage.COMPLETED,
    ]
    assert company.onboarding_status == "ready"
    assert company.workspace_metadata["discovery_state"] == "ready"
    assert company.workspace_metadata["intelligence_profile_id"]
    assert source.status == "ready"
    assert job.crawl_pages_discovered == 1
    assert job.crawl_pages_processed == 1
    assert job.snapshot_count == 1
    assert len(persistence.snapshots.items) == 1
    assert len(persistence.company_intelligence_profiles.items) == 1


def test_runner_auto_starts_discovery_without_coupling_its_failure(
    persistence: InMemoryPersistence,
) -> None:
    company_id, job_id = create_submission(persistence)
    starter = RecordingDiscoveryStarter(fail=True)

    asyncio.run(
        runner(persistence, discovery_starter=starter).run(
            job_id=job_id, organization_id="org_test"
        )
    )

    assert starter.calls == [("org_test", company_id, "user_test")]
    assert persistence.initialization_jobs.items[job_id].status == JobStatus.SUCCEEDED
    assert persistence.company_profiles.items[company_id].onboarding_status == "ready"


def test_runner_records_safe_failure_transitions(
    persistence: InMemoryPersistence,
) -> None:
    company_id, job_id = create_submission(persistence)
    persistence.sources.items.clear()

    asyncio.run(runner(persistence).run(job_id=job_id, organization_id="org_test"))

    job = persistence.initialization_jobs.items[job_id]
    company = persistence.company_profiles.items[company_id]
    assert job.status == JobStatus.FAILED
    assert job.stage == JobStage.FAILED
    assert job.error_code == "COMPANY_SOURCE_NOT_FOUND"
    assert job.error_message == "The primary company website source was not found."
    assert company.onboarding_status == "failed"


def test_graph_continues_after_partial_crawl(persistence: InMemoryPersistence) -> None:
    company_id, job_id = create_submission(persistence)
    crawler = FakeWebsiteCrawler(
        pages=[crawl_page()],
        errors=[
            CrawlError(
                code=CrawlErrorCode.CRAWL_TIMEOUT,
                message="A secondary page timed out.",
                url="https://example.com/about",
            )
        ],
    )

    asyncio.run(runner(persistence, crawler=crawler).run(job_id=job_id, organization_id="org_test"))

    job = persistence.initialization_jobs.items[job_id]
    company = persistence.company_profiles.items[company_id]
    profile = next(iter(persistence.company_intelligence_profiles.items.values()))
    assert job.status == JobStatus.SUCCEEDED
    assert company.onboarding_status == "ready"
    assert profile.extraction_status == "partial"


def test_graph_maps_terminal_crawler_failure(persistence: InMemoryPersistence) -> None:
    company_id, job_id = create_submission(persistence)
    crawler = FakeWebsiteCrawler(
        pages=[],
        errors=[
            CrawlError(
                code=CrawlErrorCode.CRAWL_TIMEOUT,
                message="The company website did not respond within the allowed time.",
                url="https://example.com",
            )
        ],
    )

    asyncio.run(runner(persistence, crawler=crawler).run(job_id=job_id, organization_id="org_test"))

    job = persistence.initialization_jobs.items[job_id]
    assert job.status == JobStatus.FAILED
    assert job.error_code == "CRAWL_TIMEOUT"
    assert persistence.company_profiles.items[company_id].onboarding_status == "failed"
    assert next(iter(persistence.sources.items.values())).status == "failed"
    assert not persistence.snapshots.items


def test_graph_preserves_robots_denied_error_code(
    persistence: InMemoryPersistence,
) -> None:
    _, job_id = create_submission(persistence)
    crawler = FakeWebsiteCrawler(
        pages=[],
        errors=[
            CrawlError(
                code=CrawlErrorCode.CRAWL_BLOCKED_BY_ROBOTS,
                message="The company website disallows this crawl through robots.txt.",
                url="https://example.com",
            )
        ],
    )

    asyncio.run(runner(persistence, crawler=crawler).run(job_id=job_id, organization_id="org_test"))

    assert persistence.initialization_jobs.items[job_id].error_code == "CRAWL_BLOCKED_BY_ROBOTS"


def test_extraction_failure_retains_persisted_snapshots(
    persistence: InMemoryPersistence,
) -> None:
    company_id, job_id = create_submission(persistence)

    asyncio.run(
        runner(persistence, extractor=FailingExtractor()).run(
            job_id=job_id, organization_id="org_test"
        )
    )

    job = persistence.initialization_jobs.items[job_id]
    assert job.status == JobStatus.FAILED
    assert job.error_code == "INTELLIGENCE_EXTRACTION_FAILED"
    assert persistence.company_profiles.items[company_id].onboarding_status == "failed"
    assert len(persistence.snapshots.items) == 1
    assert not persistence.company_intelligence_profiles.items
