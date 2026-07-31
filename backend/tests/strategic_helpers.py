from datetime import UTC, datetime

from app.domain.intelligence_evidence import (
    EvidenceKind,
    EvidenceSourceKind,
    EvidenceSourceType,
    IntelligenceCategory,
    IntelligenceEvidence,
)
from app.domain.strategy import (
    EvidenceGap,
    StrategicClaim,
    StrategicEvidenceCoverage,
    StrategicRecommendation,
    StrategicReportOutput,
    StrategicUncertainty,
)

NOW = datetime(2026, 7, 1, 12, tzinfo=UTC)


def strategy_evidence(
    identifier: str = "evidence-1",
    *,
    organization_id: str = "org_test",
    company_profile_id: str = "company-1",
    competitor_id: str = "competitor-1",
    content: str = "The public Pro plan changed from $39 to $49 per month.",
) -> IntelligenceEvidence:
    return IntelligenceEvidence(
        id=identifier,
        organization_id=organization_id,
        company_profile_id=company_profile_id,
        competitor_id=competitor_id,
        source_id=f"source-{competitor_id}",
        signal_id=f"signal-{identifier}",
        source_type=EvidenceSourceType.COMPETITOR_PRICING_SIGNAL,
        source_kind=EvidenceSourceKind.SIGNAL,
        evidence_kind=EvidenceKind.SIGNAL_SUMMARY,
        category=IntelligenceCategory.PRICING,
        source_name="Pricing",
        title="Pro plan price changed",
        section_heading="Pricing",
        content=content,
        content_hash=f"hash-{identifier}",
        processing_version="test-v1",
        version_key=f"version-{identifier}",
        active=True,
        source_url=f"https://{competitor_id}.example/pricing",
        observed_at=NOW,
        valid_from=NOW,
        created_at=NOW,
        updated_at=NOW,
        vector_id=f"vector-{identifier}",
        embedding_provider="fake",
        embedding_model="fake",
        embedding_version="fake-v1",
        embedding_dimensions=768,
    )


def strategy_output(
    evidence_id: str = "evidence-1",
    competitor_id: str = "competitor-1",
) -> StrategicReportOutput:
    claim = StrategicClaim(
        statement="The competitor increased its public Pro plan price.",
        evidence_ids=[evidence_id],
        confidence="high",
        competitor_ids=[competitor_id],
        date_from=NOW,
        date_to=NOW,
    )
    return StrategicReportOutput(
        title="Pricing response brief",
        executive_summary=(
            "Verified evidence shows a public pricing change. Recommendations remain advisory."
        ),
        situation_assessment=[claim],
        inferences=[
            StrategicClaim(
                statement="The change may create room for value-based differentiation.",
                evidence_ids=[evidence_id],
                confidence="medium",
                competitor_ids=[competitor_id],
            )
        ],
        competitor_comparisons=[],
        threats=[claim],
        opportunities=[claim],
        recommendations=[
            StrategicRecommendation(
                title="Validate packaging clarity",
                recommendation="Test clearer packaging against the observed competitor change.",
                rationale="The public pricing movement changes the comparison anchor.",
                evidence_ids=[evidence_id],
                company_context_source_ids=["company-profile:company-1"],
                competitor_ids=[competitor_id],
                impact="medium",
                effort="low",
                time_horizon="near_term",
                confidence="medium",
                assumptions=["The monitored public page remains representative."],
                risks=["A pricing response may overfit one observed change."],
                success_indicators=["Improved pricing-page comprehension in user research."],
            )
        ],
        uncertainties=[
            StrategicUncertainty(
                statement="Contract pricing and discounting are not visible.",
                evidence_ids=[evidence_id],
                impact="medium",
            )
        ],
        evidence_gaps=[
            EvidenceGap(
                topic="Private discounting",
                description="No verified private contract data is available.",
                why_it_matters="List price may not reflect realized pricing.",
                competitor_ids=[competitor_id],
            )
        ],
        evidence_coverage=StrategicEvidenceCoverage(
            evidence_count=1,
            cited_evidence_count=1,
            competitor_count=1,
            source_type_count=1,
            earliest_observed_at=NOW,
            latest_observed_at=NOW,
        ),
    )
