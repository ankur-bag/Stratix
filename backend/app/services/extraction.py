from typing import Protocol

from app.domain.intelligence import (
    CompanyIntelligenceDraft,
    CompanyIntelligenceProfile,
    ExtractionStatus,
    Snapshot,
)
from app.domain.onboarding import CompanyProfile, new_id, utc_now


class IntelligenceExtractionError(Exception):
    """Structured company intelligence could not be safely extracted."""


class CompanyIntelligenceExtractor(Protocol):
    async def extract(
        self, *, company_profile: CompanyProfile, snapshots: list[Snapshot]
    ) -> CompanyIntelligenceProfile: ...


class StructuredIntelligenceModel(Protocol):
    async def generate(
        self, *, company_profile: CompanyProfile, snapshots: list[Snapshot]
    ) -> CompanyIntelligenceDraft: ...


class DeterministicCompanyIntelligenceExtractor:
    """Build a conservative baseline from stored pages and declared context."""

    async def extract(
        self, *, company_profile: CompanyProfile, snapshots: list[Snapshot]
    ) -> CompanyIntelligenceProfile:
        if not snapshots:
            raise IntelligenceExtractionError
        snapshot_ids = [snapshot.id for snapshot in snapshots]
        homepage = next(
            (snapshot for snapshot in snapshots if snapshot.page_type == "homepage"), snapshots[0]
        )
        title = homepage.title or homepage.metadata.get("title")
        observed_name = title.split("|")[0].split("—")[0].strip() if title else None
        tagline = homepage.metadata.get("description") or homepage.metadata.get("og:description")
        headings = self._headings(snapshots)
        pricing_model = self._pricing_model(snapshots)
        integrations = [
            heading
            for heading in headings
            if "integration" in heading.casefold() or "connect" in heading.casefold()
        ][:25]
        now = utc_now()
        return CompanyIntelligenceProfile(
            id=new_id(),
            organization_id=company_profile.organization_id,
            company_profile_id=company_profile.id,
            company_name_observed=observed_name or company_profile.company_name,
            tagline=tagline,
            summary=tagline or company_profile.one_line_description,
            industries=[company_profile.industry],
            product_categories=company_profile.product_categories,
            products=[company_profile.product_name],
            target_customer_segments=company_profile.target_customer_segments,
            primary_use_cases=company_profile.primary_use_cases,
            key_features=self._deduplicate([*company_profile.key_features, *headings])[:50],
            differentiators=company_profile.differentiators,
            pricing_model=pricing_model,
            supported_regions=company_profile.target_regions,
            integrations=self._deduplicate(integrations),
            keywords=company_profile.keywords,
            source_snapshot_ids=snapshot_ids,
            extraction_status=ExtractionStatus.COMPLETED,
            extraction_confidence=0.65,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _headings(snapshots: list[Snapshot]) -> list[str]:
        values: list[str] = []
        for snapshot in snapshots:
            for line in snapshot.normalized_markdown.splitlines():
                if line.startswith("## "):
                    values.append(line.removeprefix("## ").strip())
        return DeterministicCompanyIntelligenceExtractor._deduplicate(values)

    @staticmethod
    def _pricing_model(snapshots: list[Snapshot]) -> str | None:
        pricing_text = " ".join(
            snapshot.normalized_text for snapshot in snapshots if snapshot.page_type == "pricing"
        ).casefold()
        if not pricing_text:
            return None
        if "contact sales" in pricing_text:
            return "contact_sales"
        if "free" in pricing_text and any(symbol in pricing_text for symbol in ("$", "€", "£")):
            return "freemium"
        if any(symbol in pricing_text for symbol in ("$", "€", "£")):
            return "published_pricing"
        return None

    @staticmethod
    def _deduplicate(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            if normalized and normalized.casefold() not in seen:
                seen.add(normalized.casefold())
                result.append(normalized)
        return result


class StructuredModelCompanyIntelligenceExtractor:
    """Validate one structured model response and its evidence references."""

    def __init__(self, model: StructuredIntelligenceModel) -> None:
        self._model = model

    async def extract(
        self, *, company_profile: CompanyProfile, snapshots: list[Snapshot]
    ) -> CompanyIntelligenceProfile:
        try:
            draft = await self._model.generate(company_profile=company_profile, snapshots=snapshots)
        except Exception as exc:
            raise IntelligenceExtractionError from exc
        valid_ids = {snapshot.id for snapshot in snapshots}
        if not draft.source_snapshot_ids or not set(draft.source_snapshot_ids) <= valid_ids:
            raise IntelligenceExtractionError
        now = utc_now()
        return CompanyIntelligenceProfile(
            **draft.model_dump(mode="python"),
            id=new_id(),
            organization_id=company_profile.organization_id,
            company_profile_id=company_profile.id,
            extraction_status=ExtractionStatus.COMPLETED,
            extraction_confidence=0.8,
            created_at=now,
            updated_at=now,
        )


class UnavailableCompanyIntelligenceExtractor:
    """Fail safely when model extraction is enabled without an injected provider."""

    async def extract(
        self, *, company_profile: CompanyProfile, snapshots: list[Snapshot]
    ) -> CompanyIntelligenceProfile:
        raise IntelligenceExtractionError
