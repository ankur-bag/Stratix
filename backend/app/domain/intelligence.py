from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator


class PageType(StrEnum):
    HOMEPAGE = "homepage"
    ABOUT = "about"
    PRODUCT = "product"
    FEATURES = "features"
    SOLUTIONS = "solutions"
    PRICING = "pricing"
    DOCUMENTATION = "documentation"
    CHANGELOG = "changelog"
    BLOG = "blog"
    CUSTOMERS = "customers"
    INTEGRATIONS = "integrations"
    OTHER = "other"


class NormalizedSection(BaseModel):
    heading: str | None
    content: str
    content_hash: str


class NormalizedDocument(BaseModel):
    markdown: str
    text: str
    content_hash: str
    sections: list[NormalizedSection]


class Snapshot(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str
    source_id: str
    requested_url: str
    final_url: str
    page_type: PageType
    title: str | None
    fetched_at: datetime
    http_status: int | None
    crawler_used: str
    normalized_markdown: str
    normalized_text: str
    content_hash: str
    section_hashes: list[str]
    metadata: dict[str, str]
    created_at: datetime


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class CompanyIntelligenceFields(BaseModel):
    company_name_observed: str | None = None
    tagline: str | None = None
    summary: str | None = None
    industries: list[str] = Field(default_factory=list, max_length=25)
    product_categories: list[str] = Field(default_factory=list, max_length=25)
    products: list[str] = Field(default_factory=list, max_length=50)
    target_customer_segments: list[str] = Field(default_factory=list, max_length=25)
    primary_use_cases: list[str] = Field(default_factory=list, max_length=50)
    key_features: list[str] = Field(default_factory=list, max_length=100)
    differentiators: list[str] = Field(default_factory=list, max_length=50)
    pricing_model: str | None = None
    supported_regions: list[str] = Field(default_factory=list, max_length=50)
    integrations: list[str] = Field(default_factory=list, max_length=100)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    source_snapshot_ids: list[str] = Field(min_length=1, max_length=100)


class CompanyIntelligenceDraft(CompanyIntelligenceFields):
    """Strict output accepted from deterministic or structured-model extraction."""


class CompanyIntelligenceProfile(CompanyIntelligenceFields):
    id: str
    organization_id: str
    company_profile_id: str
    extraction_status: ExtractionStatus
    extraction_confidence: float = Field(ge=0, le=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("Intelligence timestamps must be timezone-aware.")
        return self


class ReinitializeResponse(BaseModel):
    initialization_job_id: str
    status: str
    stage: str
    progress_percent: int
