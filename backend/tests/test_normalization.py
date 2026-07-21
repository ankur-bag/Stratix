from app.services.normalization import ContentNormalizer


def test_normalization_is_stable_and_suppresses_whitespace_noise() -> None:
    normalizer = ContentNormalizer()
    first = normalizer.normalize("# Product\n\n## Features\nFast   voice agents\n\nAccept cookies")
    second = normalizer.normalize(
        "  # Product  \n\n\n## Features\n Fast voice agents \nCookie preferences: accept cookies"
    )

    assert first.content_hash == second.content_hash
    assert first.text == second.text
    assert first.sections[1].content_hash == second.sections[1].content_hash
    assert "cookie" not in first.text.casefold()


def test_meaningful_content_changes_page_and_section_hashes() -> None:
    normalizer = ContentNormalizer()
    before = normalizer.normalize("# Pricing\n\n## Pro\n$49 per month")
    after = normalizer.normalize("# Pricing\n\n## Pro\n$79 per month")

    assert before.content_hash != after.content_hash
    assert before.sections[-1].content_hash != after.sections[-1].content_hash
    assert "$79" in after.text


def test_normalization_preserves_deterministic_section_order() -> None:
    document = ContentNormalizer().normalize("# Example\nIntro\n## Features\nFast\n## Pricing\n$20")

    assert [section.heading for section in document.sections] == [
        "Example",
        "Features",
        "Pricing",
    ]
