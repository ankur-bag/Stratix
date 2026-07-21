import asyncio

import pytest

from app.crawlers.models import CrawlErrorCode
from app.crawlers.policy import CrawlPolicy, CrawlPolicyViolation


class FakeResolver:
    def __init__(self, addresses: dict[str, list[str]]) -> None:
        self.addresses = addresses

    async def resolve(self, hostname: str) -> list[str]:
        return self.addresses.get(hostname, [])


def test_public_target_and_redirect_are_validated() -> None:
    policy = CrawlPolicy(FakeResolver({"example.com": ["93.184.216.34"]}))

    target = asyncio.run(policy.validate_target("https://example.com/", "https://example.com"))
    redirect = asyncio.run(
        policy.validate_redirect("https://example.com/product", "https://example.com")
    )

    assert target == "https://example.com"
    assert redirect == "https://example.com/product"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://169.254.169.254/latest/meta-data",
        "ftp://example.com",
        "https://user:secret@example.com",
    ],
)
def test_structurally_unsafe_targets_are_rejected(url: str) -> None:
    policy = CrawlPolicy(FakeResolver({"example.com": ["93.184.216.34"]}))

    with pytest.raises(CrawlPolicyViolation) as raised:
        asyncio.run(policy.validate_target(url, "https://example.com"))

    assert raised.value.code == CrawlErrorCode.INVALID_CRAWL_TARGET


def test_dns_resolution_to_private_network_is_rejected() -> None:
    policy = CrawlPolicy(FakeResolver({"example.com": ["192.168.1.20"]}))

    with pytest.raises(CrawlPolicyViolation):
        asyncio.run(policy.validate_target("https://example.com", "https://example.com"))


def test_cross_domain_redirect_is_rejected() -> None:
    policy = CrawlPolicy(
        FakeResolver(
            {
                "example.com": ["93.184.216.34"],
                "attacker.example": ["93.184.216.35"],
            }
        )
    )

    with pytest.raises(CrawlPolicyViolation):
        asyncio.run(
            policy.validate_redirect("https://attacker.example/redirect", "https://example.com")
        )


def test_link_discovery_filters_domain_prioritizes_pages_and_enforces_maximum() -> None:
    policy = CrawlPolicy(FakeResolver({}))

    links = policy.select_links(
        [
            "https://other.example/pricing",
            "https://example.com/blog/launch",
            "https://example.com/pricing",
            "https://example.com/about",
            "https://example.com/pricing/",
        ],
        "https://example.com",
        max_pages=2,
    )

    assert links == ["https://example.com/pricing", "https://example.com/about"]
