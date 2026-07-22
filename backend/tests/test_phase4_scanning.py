from datetime import UTC, datetime

import pytest
from fastapi import FastAPI

from app.api.dependencies import get_authenticated_context
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.crawlers.policy import CrawlPolicy
from app.domain.discovery import Competitor, CompetitorStatus, RelationshipType
from app.domain.intelligence import PageType
from app.domain.monitoring import CompetitorSource, CompetitorSourceType, SourceStatus
from app.domain.scanning import CompetitorScanRequest, NumericChange, ScanType
from app.domain.signals import CompetitorSignal
from app.graphs.competitor_scan import CompetitorScanGraph
from app.services.change_detection import DeterministicChangeDetector
from app.services.competitor_sources import (
    CompetitorSourceService,
    DeterministicCompetitorSourceDiscoveryService,
)
from app.services.normalization import ContentNormalizer
from app.services.scanning import CompetitorScanService, GraphCompetitorScanRunner
from app.services.signals import DeterministicSignalBuilder
from tests.fakes import (
    FakeWebsiteCrawler,
    InMemoryPersistence,
    RecordingCompetitorScanExecutor,
    crawl_page,
)


class PublicResolver:
    async def resolve(self, hostname: str) -> list[str]:
        del hostname
        return ["203.0.113.10"]


def monitored_competitor() -> Competitor:
    now = datetime.now(UTC)
    return Competitor(
        id="competitor-1",
        organization_id="org_test",
        company_profile_id="company-1",
        candidate_id="candidate-1",
        name="Acme",
        canonical_url="https://acme.example",
        canonical_domain="acme.example",
        description="A monitored competitor",
        relationship_type=RelationshipType.DIRECT,
        status=CompetitorStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        approved_at=now,
        approved_by_user_id="user_test",
        source_count=1,
    )


def pricing_source() -> CompetitorSource:
    now = datetime.now(UTC)
    return CompetitorSource(
        id="source-1",
        organization_id="org_test",
        competitor_id="competitor-1",
        type=CompetitorSourceType.PRICING,
        url="https://acme.example/pricing",
        page_type=PageType.PRICING,
        status=SourceStatus.READY,
        created_at=now,
        updated_at=now,
    )


def scan_stack(
    persistence: InMemoryPersistence, crawler: FakeWebsiteCrawler
) -> tuple[CompetitorScanService, GraphCompetitorScanRunner]:
    settings = Settings(
        app_env="testing",
        change_min_text_length=5,
        competitor_crawler_min_delay_seconds=0,
        competitor_crawler_max_delay_seconds=0,
        _env_file=None,
    )
    source_service = CompetitorSourceService(persistence)
    source_discovery = DeterministicCompetitorSourceDiscoveryService(
        persistence,
        crawler,
        CrawlPolicy(PublicResolver()),
        settings,
    )
    graph = CompetitorScanGraph(
        persistence=persistence,
        crawler=crawler,
        normalizer=ContentNormalizer(),
        change_detector=DeterministicChangeDetector(settings),
        signal_builder=DeterministicSignalBuilder(),
        source_discovery=source_discovery,
        source_service=source_service,
        settings=settings,
    )
    return (
        CompetitorScanService(persistence, RecordingCompetitorScanExecutor()),
        GraphCompetitorScanRunner(persistence, graph),
    )


@pytest.mark.anyio
async def test_baseline_unchanged_rescan_and_pricing_change() -> None:
    persistence = InMemoryPersistence()
    persistence.competitors.items["competitor-1"] = monitored_competitor()
    persistence.competitor_sources.items["source-1"] = pricing_source()
    crawler = FakeWebsiteCrawler(
        pages=[
            crawl_page(
                url="https://acme.example/pricing",
                markdown="# Pricing\n\n## Pro\n\nThe Pro plan is $49 per month.",
            )
        ]
    )
    service, runner = scan_stack(persistence, crawler)
    context = AuthenticatedContext(user_id="user_test", organization_id="org_test")

    baseline = await service.start(
        context=context,
        competitor_id="competitor-1",
        idempotency_key="baseline:test-1",
        request=CompetitorScanRequest(scan_type=ScanType.BASELINE),
    )
    await runner.run(scan_id=baseline.scan.id, organization_id="org_test")
    assert persistence.competitor_scans.items[baseline.scan.id].status == "succeeded"
    assert len(persistence.competitor_snapshots.items) == 1
    assert not persistence.competitor_signals.items

    unchanged = await service.start(
        context=context,
        competitor_id="competitor-1",
        idempotency_key="rescan:test-2",
        request=CompetitorScanRequest(),
    )
    assert unchanged.scan.scan_type == "rescan"
    await runner.run(scan_id=unchanged.scan.id, organization_id="org_test")
    assert persistence.competitor_scans.items[unchanged.scan.id].status == "succeeded"
    assert persistence.competitors.items["competitor-1"].baseline_status == "ready"
    assert len(persistence.competitor_snapshots.items) == 1
    assert not persistence.competitor_signals.items

    crawler.pages = [
        crawl_page(
            url="https://acme.example/pricing",
            markdown="# Pricing\n\n## Pro\n\nThe Pro plan is $59 per month.",
        )
    ]
    changed = await service.start(
        context=context,
        competitor_id="competitor-1",
        idempotency_key="rescan:test-3",
        request=CompetitorScanRequest(),
    )
    assert changed.scan.scan_type == "rescan"
    await runner.run(scan_id=changed.scan.id, organization_id="org_test")

    assert len(persistence.competitor_snapshots.items) == 2
    signal = next(iter(persistence.competitor_signals.items.values()))
    assert signal.type == "pricing_change"
    assert signal.numeric_changes[0].before == "49"
    assert signal.numeric_changes[0].after == "59"
    assert signal.previous_snapshot_id != signal.current_snapshot_id


def test_scan_api_is_idempotent_and_organization_scoped(
    client, persistence: InMemoryPersistence
) -> None:
    persistence.competitors.items["competitor-1"] = monitored_competitor()
    persistence.competitor_sources.items["source-1"] = pricing_source()
    headers = {"Idempotency-Key": "manual:scan-1"}
    first = client.post(
        "/api/v1/competitors/competitor-1/scans",
        headers=headers,
        json={"scan_type": "rescan", "rediscover_sources": False},
    )
    replay = client.post(
        "/api/v1/competitors/competitor-1/scans",
        headers=headers,
        json={"scan_type": "rescan", "rediscover_sources": False},
    )
    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    scan_id = first.json()["scan"]["id"]
    assert client.get(f"/api/v1/competitor-scans/{scan_id}").status_code == 200


def test_source_discovery_endpoint_reuses_scan_job(
    client,
    persistence: InMemoryPersistence,
    competitor_scan_executor: RecordingCompetitorScanExecutor,
) -> None:
    persistence.competitors.items["competitor-1"] = monitored_competitor()
    response = client.post(
        "/api/v1/competitors/competitor-1/sources/discover",
        headers={"Idempotency-Key": "discover:sources-1"},
    )
    assert response.status_code == 202
    assert response.json()["scan"]["rediscover_sources"] is True
    assert competitor_scan_executor.scheduled


def test_signal_api_filters_updates_and_isolates_organizations(
    client,
    application: FastAPI,
    persistence: InMemoryPersistence,
) -> None:
    persistence.competitors.items["competitor-1"] = monitored_competitor()
    now = datetime.now(UTC)
    signal = CompetitorSignal(
        id="signal-1",
        organization_id="org_test",
        competitor_id="competitor-1",
        source_id="source-1",
        scan_id="scan-1",
        type="pricing_change",
        title="Published pricing changed",
        summary="A published price changed.",
        observed_facts=["Published price changed from 49 to 59."],
        before_excerpt="Pro costs $49.",
        after_excerpt="Pro costs $59.",
        numeric_changes=[
            NumericChange(
                field="Published price 1",
                before="49",
                after="59",
                currency="USD",
                change_type="increase",
            )
        ],
        importance_score=0.8,
        confidence=0.95,
        source_url="https://acme.example/pricing",
        previous_snapshot_id="snapshot-before",
        current_snapshot_id="snapshot-after",
        observed_at=now,
        created_at=now,
    )
    persistence.competitor_signals.items[signal.id] = signal

    listed = client.get(
        "/api/v1/competitors/competitor-1/signals?type=pricing_change&minimum_importance=0.5"
    )
    reviewed = client.patch("/api/v1/signals/signal-1", json={"status": "reviewed"})
    invalid = client.patch("/api/v1/signals/signal-1", json={"status": "new"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == ["signal-1"]
    assert reviewed.json()["status"] == "reviewed"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_SIGNAL_STATUS"

    async def other_organization() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="other", organization_id="org_other")

    application.dependency_overrides[get_authenticated_context] = other_organization
    isolated = client.get("/api/v1/signals/signal-1")
    assert isolated.status_code == 404
    assert isolated.json()["error"]["code"] == "SIGNAL_NOT_FOUND"
