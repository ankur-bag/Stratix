from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Protocol

from app.domain.intelligence_evidence import (
    EvidenceBundle,
    IntelligenceCitation,
    IntelligenceCorrelation,
    IntelligenceSynthesis,
    IntelligenceTrend,
)


class IntelligenceSynthesizer(Protocol):
    async def synthesize(
        self,
        *,
        query: str,
        company_context: Mapping[str, str],
        evidence_bundle: EvidenceBundle,
        correlations: Sequence[IntelligenceCorrelation],
        trends: Sequence[IntelligenceTrend],
    ) -> IntelligenceSynthesis: ...


class StructuredSynthesisModel(Protocol):
    async def synthesize(
        self,
        *,
        query: str,
        company_context: Mapping[str, str],
        evidence_bundle: EvidenceBundle,
        correlations: Sequence[IntelligenceCorrelation],
        trends: Sequence[IntelligenceTrend],
    ) -> IntelligenceSynthesis: ...


class DeterministicIntelligenceSynthesizer:
    """Describe canonical evidence without recommendations or external retrieval."""

    async def synthesize(
        self,
        *,
        query: str,
        company_context: Mapping[str, str],
        evidence_bundle: EvidenceBundle,
        correlations: Sequence[IntelligenceCorrelation],
        trends: Sequence[IntelligenceTrend],
    ) -> IntelligenceSynthesis:
        del query, company_context
        items = evidence_bundle.items
        observations = [
            f"{item.title}: {item.content_excerpt}" for item in items[: min(5, len(items))]
        ]
        categories = Counter(item.signal_category.value for item in items)
        inferences = (
            [
                "The strongest evidence concentration is in "
                f"{categories.most_common(1)[0][0].replace('_', ' ')}."
            ]
            if categories
            else []
        )
        citations = [
            IntelligenceCitation(evidence_id=item.evidence_id, label=f"E{index + 1}")
            for index, item in enumerate(items)
        ]
        answer = (
            " ".join(
                f"{observation} [{citations[index].label}]"
                for index, observation in enumerate(observations)
            )
            if observations
            else "No active evidence supports an answer to this query."
        )
        self._validate_citations(citations, evidence_bundle)
        observed_dates = [item.observed_at for item in items]
        return IntelligenceSynthesis(
            answer=answer,
            observations=observations,
            inferences=inferences,
            correlations=[item.summary for item in correlations[:5]],
            historical_patterns=[
                (
                    f"{item.category.value.replace('_', ' ')}: "
                    f"{item.event_count} observed events over {item.window_days} days "
                    f"({item.direction})."
                )
                for item in trends[:5]
            ],
            uncertainties=(
                list(evidence_bundle.warnings)
                if evidence_bundle.warnings
                else ([] if items else ["No active evidence was available for this query."])
            ),
            evidence_ids=[item.evidence_id for item in items],
            competitor_ids=sorted({item.competitor_id for item in items if item.competitor_id}),
            date_range={
                "from": min(observed_dates) if observed_dates else None,
                "to": max(observed_dates) if observed_dates else None,
            },
            source_coverage=sorted(
                {item.source_type for item in items},
                key=lambda item: item.value,
            ),
            citations=citations,
            mode="deterministic",
        )

    @staticmethod
    def _validate_citations(
        citations: Sequence[IntelligenceCitation], evidence_bundle: EvidenceBundle
    ) -> None:
        allowed = {item.evidence_id for item in evidence_bundle.items}
        if any(item.evidence_id not in allowed for item in citations):
            raise ValueError("INTELLIGENCE_CITATION_INVALID")


class StructuredModelIntelligenceSynthesizer:
    """Use a bounded structured model when enabled, with deterministic fallback."""

    def __init__(
        self,
        *,
        enabled: bool,
        model: StructuredSynthesisModel | None,
        fallback: IntelligenceSynthesizer | None = None,
    ) -> None:
        self._enabled = enabled
        self._model = model
        self._fallback = fallback or DeterministicIntelligenceSynthesizer()

    async def synthesize(
        self,
        *,
        query: str,
        company_context: Mapping[str, str],
        evidence_bundle: EvidenceBundle,
        correlations: Sequence[IntelligenceCorrelation],
        trends: Sequence[IntelligenceTrend],
    ) -> IntelligenceSynthesis:
        if self._enabled and self._model is not None:
            try:
                synthesis = await self._model.synthesize(
                    query=query,
                    company_context=company_context,
                    evidence_bundle=evidence_bundle,
                    correlations=correlations,
                    trends=trends,
                )
                DeterministicIntelligenceSynthesizer._validate_citations(
                    synthesis.citations, evidence_bundle
                )
                return synthesis.model_copy(update={"mode": "structured"})
            except Exception:
                return await self._fallback.synthesize(
                    query=query,
                    company_context=company_context,
                    evidence_bundle=evidence_bundle,
                    correlations=correlations,
                    trends=trends,
                )
        return await self._fallback.synthesize(
            query=query,
            company_context=company_context,
            evidence_bundle=evidence_bundle,
            correlations=correlations,
            trends=trends,
        )


# Compatibility name for callers introduced during the Phase 6 rollout.
OptionalStructuredIntelligenceSynthesizer = StructuredModelIntelligenceSynthesizer
