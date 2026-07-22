from datetime import UTC, datetime

from app.core.config import Settings
from app.domain.intelligence import NormalizedSection, PageType
from app.domain.scanning import CompetitorSnapshot
from app.services.change_detection import DeterministicChangeDetector


def snapshot(identifier: str, text: str, sections: list[NormalizedSection]) -> CompetitorSnapshot:
    now = datetime.now(UTC)
    return CompetitorSnapshot(
        id=identifier,
        organization_id="org_test",
        competitor_id="competitor-1",
        source_id="source-1",
        scan_id=f"scan-{identifier}",
        requested_url="https://acme.example/pricing",
        final_url="https://acme.example/pricing",
        canonical_url="https://acme.example/pricing",
        page_type=PageType.PRICING,
        title="Pricing",
        fetched_at=now,
        http_status=200,
        crawler_used="fake",
        normalized_markdown=text,
        normalized_text=text,
        content_hash=identifier,
        section_hashes=[item.content_hash for item in sections],
        sections=sections,
        metadata={},
        is_baseline=identifier == "before",
        created_at=now,
    )


def section(heading: str, content: str, content_hash: str) -> NormalizedSection:
    return NormalizedSection(heading=heading, content=content, content_hash=content_hash)


def test_change_detector_ignores_moved_and_cookie_sections() -> None:
    detector = DeterministicChangeDetector(
        Settings(app_env="testing", change_min_text_length=5, _env_file=None)
    )
    previous = snapshot(
        "before",
        "Feature content Cookie consent settings",
        [
            section("Feature", "Useful feature content", "same"),
            section("Cookie preferences", "Accept all cookies", "cookie-a"),
        ],
    )
    current = snapshot(
        "after",
        "Cookie settings changed Feature content",
        [
            section("Cookie preferences", "Manage cookie consent", "cookie-b"),
            section("Feature", "Useful feature content", "same"),
        ],
    )
    change = detector.compare(previous=previous, current=current)
    assert not change.changed
    assert {item.reason for item in change.ignored_changes} >= {
        "boilerplate_section",
        "section_moved",
    }


def test_change_detector_extracts_price_and_free_tier_changes() -> None:
    detector = DeterministicChangeDetector(
        Settings(app_env="testing", change_min_text_length=5, _env_file=None)
    )
    previous = snapshot(
        "before",
        "Pro costs $49 per month.",
        [section("Pro", "Pro costs $49 per month.", "old")],
    )
    current = snapshot(
        "after",
        "Pro costs $59 per month. A free tier is available.",
        [section("Pro", "Pro costs $59 per month. A free tier is available.", "new")],
    )
    change = detector.compare(previous=previous, current=current)
    assert change.changed
    assert [item.change_type for item in change.numeric_changes] == [
        "increase",
        "free_tier_added",
    ]
