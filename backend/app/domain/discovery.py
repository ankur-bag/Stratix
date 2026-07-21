from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class DiscoveryJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"


class DiscoveryStage(StrEnum):
    QUEUED = "queued"
    LOADING_COMPANY_CONTEXT = "loading_company_context"
    BUILDING_SEARCH_QUERIES = "building_search_queries"
    SEARCHING_MARKET = "searching_market"
    NORMALIZING_CANDIDATES = "normalizing_candidates"
    CLASSIFYING_CANDIDATES = "classifying_candidates"
    RANKING_CANDIDATES = "ranking_candidates"
    SAVING_CANDIDATES = "saving_candidates"
    AWAITING_USER_REVIEW = "awaiting_user_review"
    COMPLETED = "completed"
    FAILED = "failed"


class RelationshipType(StrEnum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    ADJACENT = "adjacent"
    EMERGING = "emerging"
    UNKNOWN = "unknown"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CompetitorStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class DiscoveryJob(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str
    company_intelligence_profile_id: str
    requested_by_user_id: str
    idempotency_key: str
    status: DiscoveryJobStatus
    stage: DiscoveryStage
    progress_percent: int = Field(ge=0, le=100)
    message: str = Field(max_length=500)
    query_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    accepted_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    queries: list[str] = Field(default_factory=list, max_length=20)
    provider_error_count: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class DiscoveryEvidence(BaseModel):
    query: str
    title: str
    result_url: str
    snippet: str
    provider: str
    provider_score: float = Field(ge=0, le=1)


class CompetitorCandidate(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str
    discovery_job_id: str
    name: str
    canonical_url: str
    canonical_domain: str
    description: str
    relationship_type: RelationshipType
    status: CandidateStatus
    similarity_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)
    matching_categories: list[str] = Field(default_factory=list)
    matching_use_cases: list[str] = Field(default_factory=list)
    matching_keywords: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(min_length=1)
    evidence: list[DiscoveryEvidence] = Field(min_length=1)
    source_queries: list[str] = Field(min_length=1)
    source_result_urls: list[str] = Field(min_length=1)
    model_provider: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by_user_id: str | None = None


class Competitor(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str
    candidate_id: str
    name: str
    canonical_url: str
    canonical_domain: str
    description: str
    relationship_type: RelationshipType
    status: CompetitorStatus
    created_at: datetime
    updated_at: datetime
    approved_at: datetime
    approved_by_user_id: str


class CompetitorSource(BaseModel):
    id: str
    organization_id: str
    competitor_id: str
    type: str = "website"
    url: str
    enabled: bool = True
    status: str = "pending"
    created_at: datetime
    updated_at: datetime


class DiscoveryJobSummary(BaseModel):
    id: str
    status: DiscoveryJobStatus
    stage: DiscoveryStage
    progress_percent: int
    message: str


class DiscoveryJobResponse(BaseModel):
    job: DiscoveryJobSummary


class CandidatePage(BaseModel):
    items: list[CompetitorCandidate]
    next_cursor: str | None


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class CandidateDecision(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=100)
    decision: ReviewDecision


class CandidateReviewRequest(BaseModel):
    decisions: list[CandidateDecision] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_candidates(self) -> "CandidateReviewRequest":
        identifiers = [decision.candidate_id for decision in self.decisions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Each candidate may appear only once")
        return self


class CandidateReviewResult(BaseModel):
    candidate: CompetitorCandidate
    competitor: Competitor | None = None


class CandidateReviewResponse(BaseModel):
    results: list[CandidateReviewResult]


class CandidateClassification(BaseModel):
    canonical_domain: str
    name: str = Field(min_length=1, max_length=200)
    relationship_type: RelationshipType
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)


class CandidateClassificationBatch(BaseModel):
    candidates: list[CandidateClassification]
