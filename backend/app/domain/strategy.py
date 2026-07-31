from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrategyMode(StrEnum):
    ASK = "ask"
    COMPARE = "compare"
    EXECUTIVE_BRIEF = "executive_brief"


class StrategyRunStatus(StrEnum):
    QUEUED = "queued"
    RETRIEVING_EVIDENCE = "retrieving_evidence"
    ANALYZING = "analyzing"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


ACTIVE_STRATEGY_RUN_STATUSES = {
    StrategyRunStatus.QUEUED,
    StrategyRunStatus.RETRIEVING_EVIDENCE,
    StrategyRunStatus.ANALYZING,
    StrategyRunStatus.VALIDATING,
    StrategyRunStatus.CANCEL_REQUESTED,
}

TERMINAL_STRATEGY_RUN_STATUSES = {
    StrategyRunStatus.SUCCEEDED,
    StrategyRunStatus.PARTIALLY_SUCCEEDED,
    StrategyRunStatus.FAILED,
    StrategyRunStatus.CANCELLED,
}

Confidence = Literal["low", "medium", "high"]
Impact = Literal["low", "medium", "high"]
Effort = Literal["low", "medium", "high"]
TimeHorizon = Literal["immediate", "near_term", "medium_term"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrategyRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: StrategyMode
    query: str = Field(default="", max_length=10_000)
    competitor_ids: list[str] = Field(default_factory=list, max_length=100)
    date_from: datetime | None = None
    date_to: datetime | None = None

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("competitor_ids")
    @classmethod
    def unique_competitors(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_mode(self) -> StrategyRunCreate:
        if self.mode in {StrategyMode.ASK, StrategyMode.COMPARE} and not self.query:
            raise ValueError("A strategic question is required for this mode")
        if self.mode == StrategyMode.COMPARE and len(self.competitor_ids) < 2:
            raise ValueError("Compare mode requires at least two competitors")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
        return self


class StrategyRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    organization_id: str
    company_profile_id: str
    requested_by_user_id: str
    mode: StrategyMode
    query: str
    competitor_ids: list[str]
    date_from: datetime
    date_to: datetime
    status: StrategyRunStatus
    stage: str
    progress_percent: int = Field(ge=0, le=100)
    message: str = Field(max_length=500)
    background_job_id: str | None = None
    request_hash: str
    evidence_watermark: str
    analysis_as_of: datetime
    agent_version: str
    prompt_version: str
    tool_schema_version: str
    model_provider: str
    model_name: str
    tool_call_count: int = Field(default=0, ge=0)
    subagent_count: int = Field(default=0, ge=0)
    retrieved_evidence_count: int = Field(default=0, ge=0)
    cited_evidence_count: int = Field(default=0, ge=0)
    initial_evidence_ids: list[str] = Field(default_factory=list)
    additional_evidence_ids: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    evidence_version_keys: dict[str, str] = Field(default_factory=dict)
    report_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    updated_at: datetime


class StrategyRunSummary(BaseModel):
    id: str
    mode: StrategyMode
    query: str
    competitor_ids: list[str]
    date_from: datetime
    date_to: datetime
    status: StrategyRunStatus
    stage: str
    progress_percent: int
    message: str
    report_id: str | None
    warnings: list[str]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_run(cls, run: StrategyRun) -> StrategyRunSummary:
        return cls(**run.model_dump())


class StrategyRunResponse(BaseModel):
    run: StrategyRunSummary


class StrategyRunPage(BaseModel):
    items: list[StrategyRunSummary]
    next_cursor: str | None = None


class StrategyOperationalDiagnostics(BaseModel):
    active_runs: int = Field(ge=0)
    oldest_queued_at: datetime | None = None
    failed_runs: int = Field(ge=0)
    citation_validation_failures: int = Field(ge=0)
    average_evidence_count: float | None = Field(default=None, ge=0)
    budget_exhaustion_count: int = Field(ge=0)


class StrategicClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    statement: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: Confidence
    competitor_ids: list[str] = Field(default_factory=list, max_length=10)
    date_from: datetime | None = None
    date_to: datetime | None = None

    @field_validator("evidence_ids", "competitor_ids")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class CompetitorComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, max_length=300)
    comparison: str = Field(min_length=1, max_length=2500)
    competitor_ids: list[str] = Field(min_length=2, max_length=10)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: Confidence
    date_from: datetime | None = None
    date_to: datetime | None = None

    @field_validator("evidence_ids", "competitor_ids")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class StrategicRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=300)
    recommendation: str = Field(min_length=1, max_length=2500)
    rationale: str = Field(min_length=1, max_length=2500)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    company_context_source_ids: list[str] = Field(default_factory=list, max_length=20)
    competitor_ids: list[str] = Field(default_factory=list, max_length=10)
    impact: Impact
    effort: Effort
    time_horizon: TimeHorizon
    confidence: Confidence
    assumptions: list[str] = Field(min_length=1, max_length=10)
    risks: list[str] = Field(min_length=1, max_length=10)
    success_indicators: list[str] = Field(min_length=1, max_length=10)
    date_from: datetime | None = None
    date_to: datetime | None = None

    @field_validator(
        "evidence_ids",
        "company_context_source_ids",
        "competitor_ids",
        "assumptions",
        "risks",
        "success_indicators",
    )
    @classmethod
    def unique_items(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class StrategicUncertainty(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    statement: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    impact: Impact


class EvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=1500)
    why_it_matters: str = Field(min_length=1, max_length=1500)
    competitor_ids: list[str] = Field(default_factory=list, max_length=10)


class StrategicEvidenceCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_count: int = Field(ge=0)
    cited_evidence_count: int = Field(ge=0)
    competitor_count: int = Field(ge=0)
    source_type_count: int = Field(ge=0)
    earliest_observed_at: datetime | None = None
    latest_observed_at: datetime | None = None
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class StrategicReportOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=300)
    executive_summary: str = Field(min_length=1, max_length=4000)
    situation_assessment: list[StrategicClaim] = Field(default_factory=list, max_length=20)
    inferences: list[StrategicClaim] = Field(default_factory=list, max_length=20)
    competitor_comparisons: list[CompetitorComparison] = Field(default_factory=list, max_length=20)
    threats: list[StrategicClaim] = Field(default_factory=list, max_length=20)
    opportunities: list[StrategicClaim] = Field(default_factory=list, max_length=20)
    recommendations: list[StrategicRecommendation] = Field(default_factory=list, max_length=12)
    uncertainties: list[StrategicUncertainty] = Field(default_factory=list, max_length=20)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list, max_length=20)
    evidence_coverage: StrategicEvidenceCoverage


class StrategicReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    organization_id: str
    company_profile_id: str
    strategy_run_id: str
    mode: StrategyMode
    title: str
    executive_summary: str
    situation_assessment: list[StrategicClaim]
    inferences: list[StrategicClaim]
    competitor_comparisons: list[CompetitorComparison]
    threats: list[StrategicClaim]
    opportunities: list[StrategicClaim]
    recommendations: list[StrategicRecommendation]
    uncertainties: list[StrategicUncertainty]
    evidence_gaps: list[EvidenceGap]
    evidence_ids: list[str]
    company_context_source_ids: list[str]
    competitor_ids: list[str]
    evidence_coverage: StrategicEvidenceCoverage
    source_coverage: list[str]
    temporal_coverage: dict[str, datetime | None]
    analysis_as_of: datetime
    date_from: datetime
    date_to: datetime
    agent_version: str
    prompt_version: str
    model_provider: str
    model_name: str
    warnings: list[str] = Field(default_factory=list)
    deterministic_fallback: bool = False
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class StrategicReportView(BaseModel):
    id: str
    strategy_run_id: str
    mode: StrategyMode
    title: str
    executive_summary: str
    situation_assessment: list[StrategicClaim]
    inferences: list[StrategicClaim]
    competitor_comparisons: list[CompetitorComparison]
    threats: list[StrategicClaim]
    opportunities: list[StrategicClaim]
    recommendations: list[StrategicRecommendation]
    uncertainties: list[StrategicUncertainty]
    evidence_gaps: list[EvidenceGap]
    evidence_ids: list[str]
    competitor_ids: list[str]
    evidence_coverage: StrategicEvidenceCoverage
    source_coverage: list[str]
    temporal_coverage: dict[str, datetime | None]
    analysis_as_of: datetime
    date_from: datetime
    date_to: datetime
    warnings: list[str]
    deterministic_fallback: bool
    created_at: datetime

    @classmethod
    def from_report(cls, report: StrategicReport) -> StrategicReportView:
        return cls(**report.model_dump())


class StrategicReportPage(BaseModel):
    items: list[StrategicReportView]
    next_cursor: str | None = None


class StrategyFeedbackValue(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class StrategicReportFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    feedback: StrategyFeedbackValue
    comment: str | None = Field(default=None, max_length=1000)


class StrategicReportFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    organization_id: str
    report_id: str
    user_id: str
    feedback: StrategyFeedbackValue
    comment: str | None
    created_at: datetime
    updated_at: datetime


class StrategicReportFeedbackResponse(BaseModel):
    report_id: str
    feedback: StrategyFeedbackValue
    comment: str | None
    updated_at: datetime

    @classmethod
    def from_feedback(cls, feedback: StrategicReportFeedback) -> StrategicReportFeedbackResponse:
        return cls(**feedback.model_dump())


class StrategicAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_run_id: str
    mode: StrategyMode
    query: str
    competitor_ids: list[str]
    date_from: datetime
    date_to: datetime


class StrategicAgentContext(BaseModel):
    """Immutable, bounded data supplied to an agent invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_run_id: str
    organization_id: str
    company_profile_id: str
    requested_by_user_id: str
    analysis_as_of: datetime
    date_from: datetime
    date_to: datetime
    allowed_competitor_ids: frozenset[str]
    allowed_evidence_ids: frozenset[str]
    company_context_source_ids: frozenset[str]
    initial_context: str


class StrategicAgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: StrategicReportOutput
    tool_call_count: int = Field(default=0, ge=0)
    subagent_count: int = Field(default=0, ge=0)
    additional_evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    deterministic_fallback: bool = False
