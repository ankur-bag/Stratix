import asyncio
from typing import Any

import pytest

from app.domain.intelligence import (
    CompanyIntelligenceDraft,
    PageType,
    Snapshot,
)
from app.domain.onboarding import CompanyOnboardingRequest, CompanyProfile, utc_now
from app.services.extraction import (
    DeterministicCompanyIntelligenceExtractor,
    IntelligenceExtractionError,
    StructuredModelCompanyIntelligenceExtractor,
)
from tests.test_onboarding import valid_payload


def company() -> CompanyProfile:
    now = utc_now()
    return CompanyProfile(
        **CompanyOnboardingRequest.model_validate(valid_payload()).model_dump(mode="python"),
        id="company-1",
        organization_id="org_test",
        created_by_user_id="user_test",
        onboarding_status="initializing",
        created_at=now,
        updated_at=now,
        submitted_at=now,
    )


def snapshot(identifier: str = "snapshot-1") -> Snapshot:
    now = utc_now()
    return Snapshot(
        id=identifier,
        organization_id="org_test",
        company_profile_id="company-1",
        source_id="source-1",
        requested_url="https://example.com",
        final_url="https://example.com",
        page_type=PageType.HOMEPAGE,
        title="Observed Example | Voice platform",
        fetched_at=now,
        http_status=200,
        crawler_used="fake",
        normalized_markdown="## Features\nLow latency calls",
        normalized_text="Features\nLow latency calls",
        content_hash="hash",
        section_hashes=["section-hash"],
        metadata={"description": "Observed website tagline"},
        created_at=now,
    )


class FakeStructuredModel:
    def __init__(self, source_ids: list[str]) -> None:
        self.source_ids = source_ids

    async def generate(self, **_: Any) -> CompanyIntelligenceDraft:
        return CompanyIntelligenceDraft(
            company_name_observed="Observed Example",
            summary="Observed summary",
            source_snapshot_ids=self.source_ids,
        )


def test_deterministic_extraction_uses_stored_evidence() -> None:
    profile = asyncio.run(
        DeterministicCompanyIntelligenceExtractor().extract(
            company_profile=company(), snapshots=[snapshot()]
        )
    )

    assert profile.company_name_observed == "Observed Example"
    assert profile.tagline == "Observed website tagline"
    assert profile.source_snapshot_ids == ["snapshot-1"]
    assert "Features" in profile.key_features


def test_llm_disabled_path_is_deterministic() -> None:
    extractor = DeterministicCompanyIntelligenceExtractor()

    first = asyncio.run(extractor.extract(company_profile=company(), snapshots=[snapshot()]))
    second = asyncio.run(extractor.extract(company_profile=company(), snapshots=[snapshot()]))

    assert first.model_dump(exclude={"id", "created_at", "updated_at"}) == second.model_dump(
        exclude={"id", "created_at", "updated_at"}
    )


def test_structured_model_rejects_unknown_snapshot_references() -> None:
    extractor = StructuredModelCompanyIntelligenceExtractor(
        FakeStructuredModel(["fabricated-snapshot"])
    )

    with pytest.raises(IntelligenceExtractionError):
        asyncio.run(extractor.extract(company_profile=company(), snapshots=[snapshot()]))
