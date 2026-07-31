from datetime import UTC, datetime

from app.core.config import Settings
from app.domain.discovery import Competitor, CompetitorStatus, RelationshipType
from app.domain.intelligence import NormalizedSection, PageType
from app.domain.onboarding import CompanyProfile, OnboardingStatus
from app.domain.scanning import CompetitorSnapshot
from app.domain.signals import CompetitorSignal, SignalType
from tests.test_onboarding import valid_payload

NOW = datetime(2026, 7, 1, 12, tzinfo=UTC)


def intelligence_settings(**changes: object) -> Settings:
    return Settings(
        app_env="testing",
        embeddings_enabled=False,
        vector_search_enabled=False,
        vector_db_provider="fake",
        background_jobs_enabled=False,
        scheduler_enabled=False,
        competitor_snapshot_chunk_min_characters=20,
        competitor_snapshot_chunk_target_characters=120,
        competitor_snapshot_chunk_max_characters=180,
        competitor_snapshot_chunk_overlap_characters=20,
        _env_file=None,
        **changes,
    )


def company_profile(
    organization_id: str = "org_test", identifier: str = "company-1"
) -> CompanyProfile:
    return CompanyProfile.model_validate(
        {
            **valid_payload(),
            "id": identifier,
            "organization_id": organization_id,
            "created_by_user_id": "user_test",
            "onboarding_status": OnboardingStatus.READY,
            "created_at": NOW,
            "updated_at": NOW,
            "submitted_at": NOW,
        }
    )


def competitor(
    identifier: str = "competitor-1",
    organization_id: str = "org_test",
    company_profile_id: str = "company-1",
) -> Competitor:
    return Competitor(
        id=identifier,
        organization_id=organization_id,
        company_profile_id=company_profile_id,
        candidate_id=f"candidate-{identifier}",
        name=f"Acme {identifier}",
        canonical_url=f"https://{identifier}.example",
        canonical_domain=f"{identifier}.example",
        description="A monitored competitor",
        relationship_type=RelationshipType.DIRECT,
        status=CompetitorStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        approved_at=NOW,
        approved_by_user_id="user_test",
    )


def snapshot(
    identifier: str = "snapshot-1",
    *,
    organization_id: str = "org_test",
    competitor_id: str = "competitor-1",
    content: str = (
        "The Pro plan costs $49 per month and includes workflow automation. "
        "Enterprise customers receive advanced controls and priority support."
    ),
) -> CompetitorSnapshot:
    return CompetitorSnapshot(
        id=identifier,
        organization_id=organization_id,
        competitor_id=competitor_id,
        source_id=f"source-{competitor_id}",
        scan_id="scan-1",
        requested_url=f"https://{competitor_id}.example/pricing",
        final_url=f"https://{competitor_id}.example/pricing",
        canonical_url=f"https://{competitor_id}.example/pricing",
        page_type=PageType.PRICING,
        title="Pricing",
        fetched_at=NOW,
        http_status=200,
        crawler_used="fake",
        normalized_markdown=f"# Pricing\n\n{content}",
        normalized_text=content,
        content_hash=f"hash-{identifier}",
        section_hashes=[f"section-{identifier}"],
        sections=[
            NormalizedSection(
                heading="Pricing",
                content=content,
                content_hash=f"section-{identifier}",
            )
        ],
        metadata={},
        is_baseline=True,
        created_at=NOW,
    )


def signal(
    identifier: str = "signal-1",
    *,
    organization_id: str = "org_test",
    competitor_id: str = "competitor-1",
) -> CompetitorSignal:
    return CompetitorSignal(
        id=identifier,
        organization_id=organization_id,
        competitor_id=competitor_id,
        source_id=f"source-{competitor_id}",
        scan_id="scan-2",
        type=SignalType.PRICING_CHANGE,
        title="Pro plan price changed",
        summary="The public Pro plan price increased.",
        observed_facts=["The listed monthly price changed from $39 to $49."],
        before_excerpt="Pro costs $39 per month.",
        after_excerpt="Pro costs $49 per month.",
        changed_sections=["Pricing"],
        importance_score=0.8,
        confidence=0.95,
        source_url=f"https://{competitor_id}.example/pricing",
        previous_snapshot_id="snapshot-before",
        current_snapshot_id="snapshot-after",
        observed_at=NOW,
        created_at=NOW,
    )
