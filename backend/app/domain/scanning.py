from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.background_jobs import BackgroundJob
from app.domain.intelligence import NormalizedSection, PageType


class ScanType(StrEnum):
    BASELINE = "baseline"
    RESCAN = "rescan"


class ScanTrigger(StrEnum):
    APPROVAL = "approval"
    MANUAL = "manual"
    REINITIALIZE = "reinitialize"
    FUTURE_SCHEDULE = "future_schedule"


class ScanStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"


class ScanStage(StrEnum):
    QUEUED = "queued"
    LOADING_COMPETITOR = "loading_competitor"
    DISCOVERING_SOURCES = "discovering_sources"
    VALIDATING_SOURCES = "validating_sources"
    COLLECTING_PAGES = "collecting_pages"
    NORMALIZING_CONTENT = "normalizing_content"
    LOADING_PREVIOUS_SNAPSHOTS = "loading_previous_snapshots"
    DETECTING_CHANGES = "detecting_changes"
    SAVING_SNAPSHOTS = "saving_snapshots"
    CREATING_SIGNALS = "creating_signals"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


class CompetitorScanJob(BaseModel):
    id: str
    organization_id: str
    competitor_id: str
    requested_by_user_id: str
    scan_type: ScanType
    trigger: ScanTrigger
    status: ScanStatus
    stage: ScanStage
    progress_percent: int = Field(ge=0, le=100)
    message: str = Field(max_length=500)
    source_count: int = Field(default=0, ge=0)
    successful_source_count: int = Field(default=0, ge=0)
    failed_source_count: int = Field(default=0, ge=0)
    snapshot_count: int = Field(default=0, ge=0)
    signal_count: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    idempotency_key: str
    rediscover_sources: bool = False
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class CompetitorScanRequest(BaseModel):
    scan_type: ScanType = ScanType.RESCAN
    rediscover_sources: bool = False


class CompetitorScanResponse(BaseModel):
    scan: CompetitorScanJob
    job: BackgroundJob | None = None


class CompetitorScanPage(BaseModel):
    items: list[CompetitorScanJob]
    next_cursor: str | None = None


class CompetitorSnapshot(BaseModel):
    id: str
    organization_id: str
    competitor_id: str
    source_id: str
    scan_id: str
    requested_url: str
    final_url: str
    canonical_url: str
    page_type: PageType
    title: str | None
    fetched_at: datetime
    http_status: int | None
    crawler_used: str
    normalized_markdown: str
    normalized_text: str
    content_hash: str
    section_hashes: list[str]
    sections: list[NormalizedSection]
    metadata: dict[str, str]
    previous_snapshot_id: str | None = None
    is_baseline: bool
    created_at: datetime


class SectionChange(BaseModel):
    identifier: str
    heading: str | None
    content: str
    position: int


class ModifiedSection(BaseModel):
    identifier: str
    heading: str | None
    before: str
    after: str
    before_position: int
    after_position: int


class NumericChange(BaseModel):
    field: str
    before: str | None = None
    after: str | None = None
    currency: str | None = None
    change_type: str


class MetadataChange(BaseModel):
    field: str
    before: str | None = None
    after: str | None = None


class IgnoredChange(BaseModel):
    reason: str
    detail: str


class ChangeSet(BaseModel):
    changed: bool
    significance_score: float = Field(ge=0, le=1)
    added_sections: list[SectionChange] = Field(default_factory=list)
    removed_sections: list[SectionChange] = Field(default_factory=list)
    modified_sections: list[ModifiedSection] = Field(default_factory=list)
    numeric_changes: list[NumericChange] = Field(default_factory=list)
    metadata_changes: list[MetadataChange] = Field(default_factory=list)
    ignored_changes: list[IgnoredChange] = Field(default_factory=list)
