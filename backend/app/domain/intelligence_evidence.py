from datetime import datetime
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class EvidenceSourceType(StrEnum):
    COMPANY_DOCUMENT = "company_document"
    COMPANY_WEBSITE_SNAPSHOT = "company_website_snapshot"
    COMPETITOR_WEBSITE_SNAPSHOT = "competitor_website_snapshot"
    COMPETITOR_SIGNAL = "competitor_signal"
    COMPETITOR_PRICING_SIGNAL = "competitor_pricing_signal"
    COMPETITOR_FEATURE_SIGNAL = "competitor_feature_signal"
    COMPETITOR_POSITIONING_SIGNAL = "competitor_positioning_signal"
    COMPETITOR_INTEGRATION_SIGNAL = "competitor_integration_signal"
    COMPETITOR_CONTENT_SIGNAL = "competitor_content_signal"
    COMPETITOR_HIRING_SIGNAL = "competitor_hiring_signal"
    COMPETITOR_GITHUB_SIGNAL = "competitor_github_signal"


class EvidenceSourceKind(StrEnum):
    SNAPSHOT = "snapshot"
    SIGNAL = "signal"
    DOCUMENT = "document"


class EvidenceKind(StrEnum):
    SNAPSHOT_CHUNK = "snapshot_chunk"
    SIGNAL_SUMMARY = "signal_summary"
    SIGNAL_BEFORE_EXCERPT = "signal_before_excerpt"
    SIGNAL_AFTER_EXCERPT = "signal_after_excerpt"
    DOCUMENT_CHUNK_REFERENCE = "document_chunk_reference"
    STRUCTURED_FACT = "structured_fact"


class IntelligenceCategory(StrEnum):
    PRICING = "pricing"
    FEATURES = "features"
    POSITIONING = "positioning"
    INTEGRATIONS = "integrations"
    CONTENT = "content"
    DOCUMENTATION = "documentation"
    CUSTOMER_PROOF = "customer_proof"
    PRODUCT = "product"
    GENERAL = "general"


class IntelligenceEvidence(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str
    competitor_id: str | None = None
    source_id: str | None = None
    snapshot_id: str | None = None
    signal_id: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    chunk_index: int | None = Field(default=None, ge=0)
    source_type: EvidenceSourceType
    source_kind: EvidenceSourceKind
    evidence_kind: EvidenceKind
    category: IntelligenceCategory = IntelligenceCategory.GENERAL
    source_name: str | None = None
    title: str
    section_heading: str | None = None
    content: str
    content_hash: str
    processing_version: str
    version_key: str
    active: bool = False
    deleted: bool = False
    source_url: str | None = None
    observed_at: datetime
    published_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime
    updated_at: datetime
    page_number_start: int | None = Field(default=None, ge=1)
    page_number_end: int | None = Field(default=None, ge=1)
    vector_id: str
    embedding_provider: str
    embedding_model: str
    embedding_version: str
    embedding_dimensions: int = Field(ge=1)
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)
    deleted_at: datetime | None = None

    @model_validator(mode="after")
    def validate_source_reference(self) -> "IntelligenceEvidence":
        references = {
            EvidenceSourceKind.SNAPSHOT: self.snapshot_id,
            EvidenceSourceKind.SIGNAL: self.signal_id,
            EvidenceSourceKind.DOCUMENT: self.document_id,
        }
        if not references[self.source_kind]:
            raise ValueError("Evidence must reference its canonical source")
        if self.deleted and self.active:
            raise ValueError("Deleted evidence cannot remain active")
        return self


class IntelligenceSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    competitor_ids: list[str] = Field(default_factory=list, max_length=100)
    categories: list[IntelligenceCategory] = Field(
        default_factory=list,
        max_length=10,
        validation_alias=AliasChoices("categories", "signal_categories"),
    )
    source_types: list[EvidenceSourceType] = Field(default_factory=list, max_length=20)
    evidence_kinds: list[EvidenceKind] = Field(default_factory=list, max_length=6)
    observed_after: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("observed_after", "date_from"),
    )
    observed_before: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("observed_before", "date_to"),
    )
    include_company_context: bool = False
    limit: int = Field(default=10, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        return normalized

    @field_validator("competitor_ids")
    @classmethod
    def unique_competitors(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class EvidenceExcerpt(BaseModel):
    evidence_id: str
    competitor_id: str | None
    competitor_name: str | None = None
    source_type: EvidenceSourceType
    source_kind: EvidenceSourceKind
    evidence_kind: EvidenceKind
    signal_category: IntelligenceCategory
    title: str
    section_title: str | None
    content_excerpt: str
    source_url: str | None
    observed_at: datetime
    snapshot_id: str | None = None
    signal_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    score: float = Field(ge=-1, le=1)


class EvidenceCoverage(BaseModel):
    competitor_count: int = Field(ge=0)
    source_type_count: int = Field(ge=0)
    category_count: int = Field(ge=0)
    earliest_observed_at: datetime | None = None
    latest_observed_at: datetime | None = None


class EvidenceBundle(BaseModel):
    items: list[EvidenceExcerpt]
    grouped_by_competitor: dict[str, list[str]]
    coverage: EvidenceCoverage
    warnings: list[str] = Field(default_factory=list)


class IntelligenceSearchResponse(BaseModel):
    query: str
    bundle: EvidenceBundle


class IntelligenceCitation(BaseModel):
    evidence_id: str
    label: str


class IntelligenceAnswerRequest(IntelligenceSearchRequest):
    deterministic_only: bool = False

    @model_validator(mode="before")
    @classmethod
    def unpack_date_range(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        date_range = value.get("date_range")
        if not isinstance(date_range, dict):
            return value
        result = dict(value)
        result.setdefault("observed_after", date_range.get("from"))
        result.setdefault("observed_before", date_range.get("to"))
        return result


class IntelligenceAnswerResponse(BaseModel):
    answer: str
    observations: list[str]
    inferences: list[str]
    correlations: list[str] = Field(default_factory=list)
    historical_patterns: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    competitor_ids: list[str] = Field(default_factory=list)
    date_range: dict[str, datetime | None] = Field(default_factory=dict)
    source_coverage: list[EvidenceSourceType] = Field(default_factory=list)
    citations: list[IntelligenceCitation]
    bundle: EvidenceBundle
    synthesis_mode: str


class IntelligenceSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    observations: list[str]
    inferences: list[str]
    correlations: list[str] = Field(default_factory=list)
    historical_patterns: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    competitor_ids: list[str] = Field(default_factory=list)
    date_range: dict[str, datetime | None] = Field(default_factory=dict)
    source_coverage: list[EvidenceSourceType] = Field(default_factory=list)
    citations: list[IntelligenceCitation]
    mode: str


class CorrelationType(StrEnum):
    SAME_COMPETITOR_MULTI_SOURCE = "same_competitor_multi_source"
    MULTI_COMPETITOR_SAME_CATEGORY = "multi_competitor_same_category"
    REPEATED_CHANGE = "repeated_change_pattern"
    PRICING_DIRECTION = "pricing_direction_pattern"
    FEATURE_EXPANSION = "feature_expansion_pattern"
    POSITIONING_SHIFT = "positioning_shift_pattern"


class IntelligenceCorrelation(BaseModel):
    type: CorrelationType
    title: str
    summary: str
    competitor_ids: list[str]
    evidence_ids: list[str]
    confidence: float = Field(ge=0, le=1)


class IntelligenceTrend(BaseModel):
    window_days: int
    category: IntelligenceCategory
    event_count: int = Field(ge=0)
    competitor_count: int = Field(ge=0)
    direction: str
    evidence_ids: list[str]
    competitor_counts: dict[str, int] = Field(default_factory=dict)
    pricing_increase_count: int = Field(default=0, ge=0)
    pricing_decrease_count: int = Field(default=0, ge=0)
    scan_coverage: int = Field(default=0, ge=0)
    freshest_evidence_age_days: int | None = Field(default=None, ge=0)


class CompetitorTimelineItem(BaseModel):
    evidence_id: str
    source_type: EvidenceSourceType
    category: IntelligenceCategory
    title: str
    excerpt: str
    before_excerpt: str | None = None
    after_excerpt: str | None = None
    source_url: str | None = None
    observed_at: datetime


class CompetitorTimelinePage(BaseModel):
    items: list[CompetitorTimelineItem]
    next_cursor: str | None = None


class IntelligenceOverview(BaseModel):
    recent_changes: list[CompetitorTimelineItem]
    correlations: list[IntelligenceCorrelation]
    trends: list[IntelligenceTrend]
    warnings: list[str] = Field(default_factory=list)


class IntelligenceIndexingStatus(BaseModel):
    active_evidence_count: int = Field(ge=0)
    inactive_evidence_count: int = Field(ge=0)
    pending_job_count: int = Field(default=0, ge=0)
    failed_job_count: int = Field(ge=0)
    oldest_pending_job_age_seconds: int | None = Field(default=None, ge=0)
    active_version_consistent: bool = True
    indexing_available: bool
    last_indexed_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class IntelligenceReindexRequest(BaseModel):
    competitor_ids: list[str] = Field(default_factory=list, max_length=20)


class IntelligenceReindexResponse(BaseModel):
    job_id: str
    status: str
