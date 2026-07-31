from __future__ import annotations

from collections.abc import Iterable

from app.agents.errors import StrategicAgentError
from app.domain.intelligence_evidence import IntelligenceEvidence
from app.domain.strategy import (
    EvidenceGap,
    StrategicAgentContext,
    StrategicClaim,
    StrategicEvidenceCoverage,
    StrategicReportOutput,
    StrategicUncertainty,
)
from app.repositories.interfaces import Persistence


def cited_evidence_ids(report: StrategicReportOutput) -> list[str]:
    identifiers: list[str] = []
    claim_groups: Iterable[Iterable[StrategicClaim]] = (
        report.situation_assessment,
        report.inferences,
        report.threats,
        report.opportunities,
    )
    for claims in claim_groups:
        for claim in claims:
            identifiers.extend(claim.evidence_ids)
    for comparison in report.competitor_comparisons:
        identifiers.extend(comparison.evidence_ids)
    for recommendation in report.recommendations:
        identifiers.extend(recommendation.evidence_ids)
    for uncertainty in report.uncertainties:
        identifiers.extend(uncertainty.evidence_ids)
    return list(dict.fromkeys(identifiers))


async def validate_strategic_report(
    *,
    report: StrategicReportOutput,
    context: StrategicAgentContext,
    additional_evidence_ids: list[str],
    persistence: Persistence,
) -> list[IntelligenceEvidence]:
    allowed = set(context.allowed_evidence_ids) | set(additional_evidence_ids)
    cited = cited_evidence_ids(report)
    if any(evidence_id not in allowed for evidence_id in cited):
        raise StrategicAgentError(
            "STRATEGIC_AGENT_CITATION_INVALID",
            "The strategic report cited evidence outside the authorized run.",
        )
    canonical = await persistence.intelligence_evidence.get_many(context.organization_id, cited)
    by_id = {item.id: item for item in canonical}
    if len(by_id) != len(cited):
        raise StrategicAgentError(
            "STRATEGIC_AGENT_CITATION_INVALID",
            "One or more strategic citations are no longer active.",
        )
    cutoff = min(context.date_to, context.analysis_as_of)
    for evidence_id in cited:
        item = by_id[evidence_id]
        if (
            item.organization_id != context.organization_id
            or item.company_profile_id != context.company_profile_id
            or item.competitor_id not in context.allowed_competitor_ids
            or not context.date_from <= item.observed_at <= cutoff
            or item.deleted
            or not item.active
        ):
            raise StrategicAgentError(
                "STRATEGIC_AGENT_CITATION_INVALID",
                "A strategic citation is outside the selected scope.",
            )
    allowed_competitors = set(context.allowed_competitor_ids)
    for competitor_ids in _competitor_id_groups(report):
        if not set(competitor_ids).issubset(allowed_competitors):
            raise StrategicAgentError(
                "STRATEGIC_AGENT_OUTPUT_INVALID",
                "The strategic report referenced an unsupported competitor.",
            )
    for recommendation in report.recommendations:
        if not set(recommendation.company_context_source_ids).issubset(
            context.company_context_source_ids
        ):
            raise StrategicAgentError(
                "STRATEGIC_AGENT_OUTPUT_INVALID",
                "A recommendation referenced unsupported company context.",
            )
    return [by_id[evidence_id] for evidence_id in cited]


def deterministic_fallback_report(
    *,
    evidence: list[IntelligenceEvidence],
    reason: str,
    competitor_count: int,
    truncated: bool,
) -> StrategicReportOutput:
    ordered = sorted(evidence, key=lambda item: (item.observed_at, item.id), reverse=True)
    observations = [
        StrategicClaim(
            statement=f"{item.title}: {item.content[:900]}",
            evidence_ids=[item.id],
            confidence="high",
            competitor_ids=[item.competitor_id] if item.competitor_id else [],
            date_from=item.observed_at,
            date_to=item.observed_at,
        )
        for item in ordered[:8]
        if item.competitor_id
    ]
    source_types = {item.source_type for item in ordered}
    warnings = [reason]
    if truncated:
        warnings.append("The evidence bundle reached its configured limit.")
    summary = (
        "A deterministic evidence summary is shown because the strategic model did "
        "not produce a validated report. No AI-generated strategic recommendations "
        "were created."
    )
    return StrategicReportOutput(
        title="Verified competitor evidence summary",
        executive_summary=summary,
        situation_assessment=observations,
        inferences=[],
        competitor_comparisons=[],
        threats=[],
        opportunities=[],
        recommendations=[],
        uncertainties=[
            StrategicUncertainty(
                statement=reason,
                evidence_ids=[],
                impact="high",
            )
        ],
        evidence_gaps=[
            EvidenceGap(
                topic="Strategic interpretation unavailable",
                description=(
                    "The available evidence was retained, but a validated strategic "
                    "interpretation could not be generated."
                ),
                why_it_matters=("Recommendations require a configured model and valid citations."),
                competitor_ids=[],
            )
        ],
        evidence_coverage=StrategicEvidenceCoverage(
            evidence_count=len(ordered),
            cited_evidence_count=len(observations),
            competitor_count=competitor_count,
            source_type_count=len(source_types),
            earliest_observed_at=min((item.observed_at for item in ordered), default=None),
            latest_observed_at=max((item.observed_at for item in ordered), default=None),
            truncated=truncated,
            warnings=warnings,
        ),
    )


def _competitor_id_groups(report: StrategicReportOutput) -> Iterable[list[str]]:
    for items in (
        report.situation_assessment,
        report.inferences,
        report.threats,
        report.opportunities,
        report.competitor_comparisons,
        report.recommendations,
        report.evidence_gaps,
    ):
        for item in items:
            yield item.competitor_ids
