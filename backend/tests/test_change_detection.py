import hashlib
from datetime import UTC, datetime

from app.core.config import Settings
from app.domain.intelligence import NormalizedSection, PageType
from app.domain.scanning import ChangeSet, CompetitorSnapshot, NumericChange
from app.services.change_detection import DeterministicChangeDetector


def section(heading: str, content: str) -> NormalizedSection:
    canonical = f"{heading}\n{content}"
    return NormalizedSection(
        heading=heading,
        content=content,
        content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def snapshot(identifier: str, sections: list[NormalizedSection]) -> CompetitorSnapshot:
    now = datetime.now(UTC)
    text = "\n\n".join(f"{item.heading}\n{item.content}" for item in sections)
    return CompetitorSnapshot(
        id=identifier,
        organization_id="org-test",
        competitor_id="competitor-test",
        source_id="source-test",
        scan_id=f"scan-{identifier}",
        requested_url="https://example.com/pricing",
        final_url="https://example.com/pricing",
        canonical_url="https://example.com/pricing",
        page_type=PageType.PRICING,
        title="Pricing",
        fetched_at=now,
        http_status=200,
        crawler_used="controlled-fixture",
        normalized_markdown=text,
        normalized_text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        section_hashes=[item.content_hash for item in sections],
        sections=sections,
        metadata={},
        is_baseline=identifier == "before",
        created_at=now,
    )


def compare(previous: list[NormalizedSection], current: list[NormalizedSection]) -> ChangeSet:
    detector = DeterministicChangeDetector(
        Settings(app_env="testing", change_min_text_length=5, _env_file=None)
    )
    return detector.compare(
        previous=snapshot("before", previous),
        current=snapshot("after", current),
    )


def change(changes: list[NumericChange], change_type: str, field_prefix: str) -> NumericChange:
    return next(
        item
        for item in changes
        if item.change_type == change_type and item.field.casefold().startswith(field_prefix)
    )


def test_exact_acceptance_fixture_produces_five_plan_aware_semantics() -> None:
    result = compare(
        [
            section("Starter plan", "Free"),
            section("Pro plan", "$49/month"),
            section("Voice support", "unavailable"),
        ],
        [
            section("Starter plan", "$19/month"),
            section("Pro plan", "$59/month"),
            section("Business plan", "$129/month"),
            section("Voice support", "available"),
        ],
    )

    starter_free = change(result.numeric_changes, "free_tier_removed", "starter")
    starter_price = change(result.numeric_changes, "price_added", "starter")
    pro_price = change(result.numeric_changes, "increase", "pro")
    business = change(result.numeric_changes, "plan_added", "business")
    voice = next(item for item in result.modified_sections if item.heading == "Voice support")

    assert starter_free.before == "available" and starter_free.after == "not observed"
    assert starter_price.after == "19" and starter_price.currency == "USD"
    assert pro_price.before == "49" and pro_price.after == "59"
    assert business.after == "129" and business.currency == "USD"
    assert voice.before == "unavailable" and voice.after == "available"
    assert not any(
        item.change_type == "decrease" and item.before == "49" and item.after == "19"
        for item in result.numeric_changes
    )
    assert len(result.numeric_changes) == 4


def test_reordered_pricing_cards_with_unchanged_prices_create_no_change() -> None:
    result = compare(
        [section("Starter", "$19/month"), section("Pro", "$59/month")],
        [section("Pro", "$59/month"), section("Starter", "$19/month")],
    )

    assert not result.changed
    assert result.numeric_changes == []
    assert "section_moved" in {item.reason for item in result.ignored_changes}


def test_plan_inserted_in_middle_does_not_shift_other_plan_prices() -> None:
    result = compare(
        [section("Starter", "$19/month"), section("Pro", "$59/month")],
        [
            section("Starter", "$19/month"),
            section("Business", "$129/month"),
            section("Pro", "$59/month"),
        ],
    )

    assert [(item.change_type, item.field, item.after) for item in result.numeric_changes] == [
        ("plan_added", "Business monthly price", "129")
    ]


def test_plan_removal_and_rename_are_identity_aware() -> None:
    removed = compare(
        [section("Starter", "$19/month"), section("Pro", "$59/month")],
        [section("Pro", "$59/month")],
    )
    renamed = compare(
        [section("Basic plan", "$19/month")],
        [section("Starter plan", "$19/month")],
    )

    assert [(item.change_type, item.before) for item in removed.numeric_changes] == [
        ("plan_removed", "19")
    ]
    assert [(item.change_type, item.before, item.after) for item in renamed.numeric_changes] == [
        ("plan_renamed", "Basic", "Starter")
    ]


def test_billing_periods_and_currencies_are_compared_within_the_same_plan() -> None:
    result = compare(
        [section("Pro", "$49/month\n$490/year\n45 EUR/month")],
        [section("Pro", "45 EUR/month\n$59/month\n$490/year")],
    )

    assert [
        (item.change_type, item.before, item.after, item.currency)
        for item in result.numeric_changes
    ] == [("increase", "49", "59", "USD")]


def test_monthly_and_annual_offers_do_not_create_changes_when_reordered() -> None:
    result = compare(
        [section("Pro", "$49/month\n$490/year")],
        [section("Pro", "$490/year\n$49/month")],
    )

    assert result.numeric_changes == []


def test_contact_sales_plans_remain_stable_and_are_detected_when_added() -> None:
    stable = compare(
        [section("Enterprise", "Contact sales")],
        [section("Enterprise", "Talk to sales")],
    )
    added = compare(
        [section("Pro", "$59/month")],
        [section("Pro", "$59/month"), section("Enterprise", "Contact sales")],
    )

    assert stable.numeric_changes == []
    assert [(item.change_type, item.after) for item in added.numeric_changes] == [
        ("plan_added", "contact sales")
    ]


def test_free_variants_and_paid_to_free_transitions() -> None:
    free_variants = ["Free", "Free plan", "Free tier", "$0/month", "0/month", "No cost"]
    for value in free_variants:
        result = compare([section("Starter", value)], [section("Starter", "$19/month")])
        assert {item.change_type for item in result.numeric_changes} == {
            "free_tier_removed",
            "price_added",
        }

    paid_to_free = compare(
        [section("Starter", "$19/month")],
        [section("Starter", "Free")],
    )
    assert {item.change_type for item in paid_to_free.numeric_changes} == {
        "free_tier_added",
        "price_removed",
    }


def test_table_rows_and_labelled_blocks_associate_prices_with_plan_names() -> None:
    result = compare(
        [section("Pricing", "| Starter | $19/month |\n| Pro | $49/month |")],
        [section("Pricing", "| Starter | $19/month |\n| Pro | $59/month |")],
    )

    pro = change(result.numeric_changes, "increase", "pro")
    assert pro.before == "49" and pro.after == "59"


def test_duplicate_promotional_price_is_deduplicated() -> None:
    result = compare(
        [section("Pro", "$49/month\n$49/month")],
        [section("Pro", "$49/month")],
    )

    assert result.numeric_changes == []


def test_unrelated_numbers_and_unlabelled_prices_do_not_emit_confident_price_changes() -> None:
    unrelated = compare(
        [section("Limits", "Up to 50 users and 20% usage growth")],
        [section("Limits", "Up to 100 users and 25% usage growth")],
    )
    unlabelled = compare(
        [section("Pricing", "$49/month")],
        [section("Pricing", "$19/month")],
    )

    assert unrelated.numeric_changes == []
    assert unlabelled.numeric_changes == []
    assert "low_confidence_pricing" in {item.reason for item in unlabelled.ignored_changes}


def test_conflicting_promotional_prices_are_marked_ambiguous() -> None:
    result = compare(
        [section("Pro", "$49/month"), section("Pro", "$39/month")],
        [section("Pro", "$59/month")],
    )

    assert result.numeric_changes == []
    assert "ambiguous_pricing" in {item.reason for item in result.ignored_changes}
