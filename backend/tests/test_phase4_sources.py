from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.crawlers.policy import CrawlPolicy
from app.domain.discovery import Competitor, CompetitorStatus, RelationshipType
from app.services.competitor_sources import (
    DeterministicCompetitorSourceDiscoveryService,
)
from tests.fakes import FakeWebsiteCrawler, InMemoryPersistence, crawl_page


class UnusedResolver:
    async def resolve(self, hostname: str) -> list[str]:
        return ["203.0.113.10"]


def competitor(*, organization_id: str = "org_test") -> Competitor:
    now = datetime.now(UTC)
    return Competitor(
        id="competitor-1",
        organization_id=organization_id,
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
    )


def test_source_crud_validation_and_disable(client, persistence: InMemoryPersistence) -> None:
    persistence.competitors.items["competitor-1"] = competitor()

    created = client.post(
        "/api/v1/competitors/competitor-1/sources",
        json={"url": "https://acme.example/pricing?utm_source=test", "priority": "high"},
    )
    assert created.status_code == 201
    source = created.json()
    assert source["canonical_url"] == "https://acme.example/pricing"
    assert source["type"] == "pricing"

    duplicate = client.post(
        "/api/v1/competitors/competitor-1/sources",
        json={"url": "https://acme.example/pricing"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "COMPETITOR_SOURCE_ALREADY_EXISTS"

    mismatch = client.post(
        "/api/v1/competitors/competitor-1/sources",
        json={"url": "https://other.example/pricing"},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "COMPETITOR_SOURCE_DOMAIN_MISMATCH"

    unsafe = client.post(
        "/api/v1/competitors/competitor-1/sources",
        json={"url": "http://127.0.0.1/admin"},
    )
    assert unsafe.status_code == 422
    assert unsafe.json()["error"]["code"] == "INVALID_COMPETITOR_SOURCE_URL"

    disabled = client.delete(f"/api/v1/competitors/competitor-1/sources/{source['id']}")
    assert disabled.status_code == 204
    stored = persistence.competitor_sources.items[source["id"]]
    assert not stored.enabled
    assert stored.status == "disabled"


def test_source_listing_is_paginated_and_organization_scoped(
    client, persistence: InMemoryPersistence
) -> None:
    persistence.competitors.items["competitor-1"] = competitor()
    persistence.competitors.items["other"] = competitor(organization_id="org_other").model_copy(
        update={"id": "other", "candidate_id": "candidate-other"}
    )
    for path in ("pricing", "features", "about"):
        response = client.post(
            "/api/v1/competitors/competitor-1/sources",
            json={"url": f"https://acme.example/{path}"},
        )
        assert response.status_code == 201
    page = client.get("/api/v1/competitors/competitor-1/sources?limit=2")
    assert page.status_code == 200
    assert len(page.json()["items"]) == 2
    assert page.json()["next_cursor"]
    hidden = client.get("/api/v1/competitors/other/sources")
    assert hidden.status_code == 404


@pytest.mark.anyio
async def test_source_discovery_is_deterministic_same_domain_and_bounded() -> None:
    persistence = InMemoryPersistence()
    persistence.competitors.items["competitor-1"] = competitor()
    crawler = FakeWebsiteCrawler(
        pages=[
            crawl_page(
                url="https://acme.example",
                links=[
                    "https://acme.example/login",
                    "https://other.example/pricing",
                    "https://acme.example/pricing?utm_campaign=x",
                    "https://docs.acme.example/documentation",
                    "https://acme.example/assets/guide.pdf",
                    "https://acme.example/features",
                ],
            )
        ]
    )
    service = DeterministicCompetitorSourceDiscoveryService(
        persistence,
        crawler,
        CrawlPolicy(UnusedResolver()),
        Settings(
            app_env="testing",
            competitor_crawler_max_sources=3,
            _env_file=None,
        ),
    )
    first = await service.discover(organization_id="org_test", competitor_id="competitor-1")
    second = await service.discover(organization_id="org_test", competitor_id="competitor-1")
    assert first == second
    assert [item.page_type for item in first] == ["website", "pricing", "features"]
    assert all("other.example" not in item.url and "/login" not in item.url for item in first)
