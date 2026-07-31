from app.domain.intelligence_evidence import (
    EvidenceKind,
    EvidenceSourceKind,
    EvidenceSourceType,
    IntelligenceCategory,
    IntelligenceEvidence,
)
from app.services.intelligence import IntelligenceSearchService
from tests.intelligence_helpers import NOW


def evidence(
    identifier: str,
    competitor_id: str,
    source_type: EvidenceSourceType,
    *,
    organization_id: str = "org",
    category: IntelligenceCategory = IntelligenceCategory.PRICING,
    content: str = "Public pricing evidence.",
) -> IntelligenceEvidence:
    return IntelligenceEvidence(
        id=identifier,
        organization_id=organization_id,
        company_profile_id="company",
        competitor_id=competitor_id,
        snapshot_id=f"snapshot-{identifier}",
        source_type=source_type,
        source_kind=EvidenceSourceKind.SNAPSHOT,
        evidence_kind=EvidenceKind.SNAPSHOT_CHUNK,
        category=category,
        title="Pricing",
        content=content,
        content_hash=identifier,
        processing_version="v1",
        version_key="v1",
        active=True,
        observed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        vector_id=f"vector-{identifier}",
        embedding_provider="fake",
        embedding_model="fake",
        embedding_version="fake",
        embedding_dimensions=768,
    )


def test_correlation_rules_are_deterministic_and_cited() -> None:
    items = [
        evidence("a", "one", EvidenceSourceType.COMPETITOR_WEBSITE_SNAPSHOT),
        evidence("b", "one", EvidenceSourceType.COMPETITOR_PRICING_SIGNAL),
        evidence("c", "two", EvidenceSourceType.COMPETITOR_WEBSITE_SNAPSHOT),
    ]
    first = IntelligenceSearchService._correlations(items)
    assert first == IntelligenceSearchService._correlations(items)
    assert any(item.type.value == "same_competitor_multi_source" for item in first)
    assert any(item.type.value == "multi_competitor_same_category" for item in first)
    assert all(item.evidence_ids for item in first)


def test_correlation_detects_pricing_direction_and_repeated_feature_activity() -> None:
    items = [
        evidence(
            f"price-{index}",
            "one",
            EvidenceSourceType.COMPETITOR_PRICING_SIGNAL,
            content=f"Before: ${20 + index}\nAfter: ${30 + index}",
        )
        for index in range(3)
    ] + [
        evidence(
            f"feature-{index}",
            "two",
            EvidenceSourceType.COMPETITOR_FEATURE_SIGNAL,
            category=IntelligenceCategory.FEATURES,
        )
        for index in range(3)
    ]

    correlations = IntelligenceSearchService._correlations(items)

    assert any(
        item.type.value == "pricing_direction_pattern" and "increase" in item.summary
        for item in correlations
    )
    assert any(item.type.value == "feature_expansion_pattern" for item in correlations)


def test_correlation_never_groups_evidence_across_organizations() -> None:
    items = [
        evidence(
            "a",
            "one",
            EvidenceSourceType.COMPETITOR_WEBSITE_SNAPSHOT,
            organization_id="org-a",
        ),
        evidence(
            "b",
            "two",
            EvidenceSourceType.COMPETITOR_WEBSITE_SNAPSHOT,
            organization_id="org-b",
        ),
    ]

    correlations = IntelligenceSearchService._correlations(items)

    assert not any(item.type.value == "multi_competitor_same_category" for item in correlations)
