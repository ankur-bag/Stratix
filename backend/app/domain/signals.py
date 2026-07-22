from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from app.domain.scanning import NumericChange


class SignalType(StrEnum):
    PRICING_CHANGE = "pricing_change"
    FEATURE_LAUNCH = "feature_launch"
    FEATURE_REMOVAL = "feature_removal"
    PRODUCT_CHANGE = "product_change"
    POSITIONING_CHANGE = "positioning_change"
    DOCUMENTATION_CHANGE = "documentation_change"
    CHANGELOG_UPDATE = "changelog_update"
    INTEGRATION_CHANGE = "integration_change"
    CUSTOMER_PROOF_CHANGE = "customer_proof_change"
    CONTENT_CHANGE = "content_change"
    UNKNOWN = "unknown"


class SignalStatus(StrEnum):
    NEW = "new"
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"


class CompetitorSignal(BaseModel):
    id: str
    organization_id: str
    competitor_id: str
    source_id: str
    scan_id: str
    type: SignalType
    title: str
    summary: str
    observed_facts: list[str] = Field(min_length=1)
    before_excerpt: str | None = None
    after_excerpt: str
    changed_sections: list[str] = Field(default_factory=list)
    numeric_changes: list[NumericChange] = Field(default_factory=list)
    importance_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    status: SignalStatus = SignalStatus.NEW
    source_url: str
    previous_snapshot_id: str
    current_snapshot_id: str
    observed_at: datetime
    created_at: datetime

    @model_validator(mode="after")
    def validate_evidence(self) -> "CompetitorSignal":
        if self.previous_snapshot_id == self.current_snapshot_id:
            raise ValueError("Signal snapshots must identify different versions")
        if not self.after_excerpt.strip():
            raise ValueError("Signal evidence must include after content")
        return self


class SignalStatusUpdate(BaseModel):
    status: SignalStatus

    @model_validator(mode="after")
    def reject_new_status(self) -> "SignalStatusUpdate":
        if self.status == SignalStatus.NEW:
            raise ValueError("Signals may only be reviewed or dismissed")
        return self


class SignalPage(BaseModel):
    items: list[CompetitorSignal]
    next_cursor: str | None = None
