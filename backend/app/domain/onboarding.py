import re
from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import ip_address
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


def new_id() -> str:
    """Return a database-safe public identifier."""
    return str(uuid4())


def _normalize_list(value: object) -> object:
    if not isinstance(value, list):
        return value
    result: list[object] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            result.append(item)
            continue
        normalized = item.strip()
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        result.append(normalized)
    return result


def normalize_website_url(value: str) -> str:
    """Normalize a public HTTP(S) URL without performing network access."""
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Website URL is malformed.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Website URL must use HTTP or HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Website URL must not contain credentials.")
    hostname = parsed.hostname.rstrip(".").lower()
    blocked_names = {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.aws.internal",
        "metadata.azure.internal",
    }
    blocked_suffixes = (".localhost", ".local", ".internal", ".lan", ".home")
    if hostname in blocked_names or hostname.endswith(blocked_suffixes):
        raise ValueError("Website URL must identify a public destination.")
    try:
        address = ip_address(hostname)
    except ValueError:
        address = None
    if address is None:
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Website URL contains an invalid hostname.") from exc
        labels = hostname.split(".")
        if len(labels) < 2 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
        ):
            raise ValueError("Website URL contains an invalid public hostname.")
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("Website URL must identify a public destination.")
    if hostname in {"169.254.169.254", "100.100.100.200"}:
        raise ValueError("Website URL must not identify a cloud metadata destination.")
    scheme = parsed.scheme.lower()
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    rendered_host = f"[{hostname}]" if address is not None and address.version == 6 else hostname
    netloc = rendered_host if port is None or default_port else f"{rendered_host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


class BusinessStage(StrEnum):
    IDEA = "idea"
    PRE_SEED = "pre_seed"
    SEED = "seed"
    SERIES_A = "series_a"
    GROWTH = "growth"
    ESTABLISHED = "established"


class BusinessModel(StrEnum):
    SAAS = "saas"
    MARKETPLACE = "marketplace"
    ECOMMERCE = "ecommerce"
    SERVICES = "services"
    CONSUMER = "consumer"
    OPEN_SOURCE = "open_source"
    HYBRID = "hybrid"
    OTHER = "other"


class OnboardingStatus(StrEnum):
    SUBMITTED = "submitted"
    INITIALIZING = "initializing"
    READY = "ready"
    FAILED = "failed"


class SourceType(StrEnum):
    WEBSITE = "website"


class SourceStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    DISABLED = "disabled"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobStage(StrEnum):
    QUEUED = "queued"
    VALIDATING_COMPANY = "validating_company"
    CREATING_COMPANY_SOURCE = "creating_company_source"
    PREPARING_INTELLIGENCE_WORKSPACE = "preparing_intelligence_workspace"
    READY_FOR_DISCOVERY = "ready_for_discovery"
    COMPLETED = "completed"
    FAILED = "failed"


class CompanyFields(BaseModel):
    """Editable and normalized company profile fields."""

    model_config = ConfigDict(str_strip_whitespace=True)

    company_name: str = Field(min_length=1, max_length=200)
    website_url: str = Field(max_length=2048)
    one_line_description: str = Field(min_length=1, max_length=500)
    detailed_description: str | None = Field(default=None, max_length=5000)
    industry: str = Field(min_length=1, max_length=200)
    sub_industry: str | None = Field(default=None, max_length=200)
    business_stage: BusinessStage
    business_model: BusinessModel | None = None
    headquarters_country: str | None = Field(default=None, max_length=100)
    target_regions: list[str] = Field(default_factory=list, max_length=25)
    target_customer_segments: list[str] = Field(min_length=1, max_length=25)
    product_name: str = Field(min_length=1, max_length=200)
    product_categories: list[str] = Field(default_factory=list, max_length=25)
    primary_use_cases: list[str] = Field(min_length=1, max_length=25)
    key_features: list[str] = Field(default_factory=list, max_length=50)
    differentiators: list[str] = Field(default_factory=list, max_length=25)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    known_competitors: list[str] = Field(default_factory=list, max_length=50)
    strategic_goals: list[str] = Field(min_length=1, max_length=25)

    @field_validator("website_url")
    @classmethod
    def validate_website_url(cls, value: str) -> str:
        return normalize_website_url(value)

    @field_validator(
        "target_regions",
        "target_customer_segments",
        "product_categories",
        "primary_use_cases",
        "key_features",
        "differentiators",
        "keywords",
        "known_competitors",
        "strategic_goals",
        mode="before",
    )
    @classmethod
    def normalize_lists(cls, value: object) -> object:
        return _normalize_list(value)

    @field_validator(
        "target_regions",
        "target_customer_segments",
        "product_categories",
        "primary_use_cases",
        "key_features",
        "differentiators",
        "keywords",
        "known_competitors",
        "strategic_goals",
    )
    @classmethod
    def validate_list_items(cls, value: list[str]) -> list[str]:
        if any(len(item) > 300 for item in value):
            raise ValueError("List items must not exceed 300 characters.")
        return value


class CompanyOnboardingRequest(CompanyFields):
    """Payload accepted for the first company onboarding submission."""


class CompanyProfile(CompanyFields):
    """Persisted primary company profile for an organization."""

    id: str
    organization_id: str
    created_by_user_id: str
    onboarding_status: OnboardingStatus
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime
    workspace_metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        for value in (self.created_at, self.updated_at, self.submitted_at):
            if value.tzinfo is None:
                raise ValueError("Timestamps must be timezone-aware.")
        return self


class CompanyUpdateRequest(BaseModel):
    """Editable subset of a company profile."""

    model_config = ConfigDict(str_strip_whitespace=True)

    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    website_url: str | None = Field(default=None, max_length=2048)
    one_line_description: str | None = Field(default=None, min_length=1, max_length=500)
    detailed_description: str | None = Field(default=None, max_length=5000)
    industry: str | None = Field(default=None, min_length=1, max_length=200)
    sub_industry: str | None = Field(default=None, max_length=200)
    business_stage: BusinessStage | None = None
    business_model: BusinessModel | None = None
    headquarters_country: str | None = Field(default=None, max_length=100)
    target_regions: list[str] | None = Field(default=None, max_length=25)
    target_customer_segments: list[str] | None = Field(default=None, min_length=1, max_length=25)
    product_name: str | None = Field(default=None, min_length=1, max_length=200)
    product_categories: list[str] | None = Field(default=None, max_length=25)
    primary_use_cases: list[str] | None = Field(default=None, min_length=1, max_length=25)
    key_features: list[str] | None = Field(default=None, max_length=50)
    differentiators: list[str] | None = Field(default=None, max_length=25)
    keywords: list[str] | None = Field(default=None, max_length=50)
    known_competitors: list[str] | None = Field(default=None, max_length=50)
    strategic_goals: list[str] | None = Field(default=None, min_length=1, max_length=25)

    @field_validator("website_url")
    @classmethod
    def validate_website_url(cls, value: str | None) -> str | None:
        return normalize_website_url(value) if value is not None else None

    @field_validator(
        "target_regions",
        "target_customer_segments",
        "product_categories",
        "primary_use_cases",
        "key_features",
        "differentiators",
        "keywords",
        "known_competitors",
        "strategic_goals",
        mode="before",
    )
    @classmethod
    def normalize_lists(cls, value: object) -> object:
        return _normalize_list(value)


class Source(BaseModel):
    """Persisted source associated with a company profile."""

    id: str
    organization_id: str
    company_profile_id: str
    type: SourceType
    url: str
    enabled: bool
    status: SourceStatus
    last_scanned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InitializationJob(BaseModel):
    """Persisted progress record for company workspace initialization."""

    id: str
    organization_id: str
    company_profile_id: str
    requested_by_user_id: str
    status: JobStatus
    stage: JobStage
    progress_percent: int = Field(ge=0, le=100)
    message: str = Field(max_length=500)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class IdempotencyRecord(BaseModel):
    """Stored reference for an organization-scoped idempotent operation."""

    id: str
    organization_id: str
    idempotency_key: str
    request_fingerprint: str
    company_profile_id: str
    initialization_job_id: str
    created_at: datetime
    expires_at: datetime


class CompanySummary(BaseModel):
    id: str
    company_name: str
    website_url: str
    onboarding_status: OnboardingStatus


class JobSummary(BaseModel):
    id: str
    status: JobStatus
    stage: JobStage
    progress_percent: int
    message: str | None = None


class OnboardingResponse(BaseModel):
    company_profile: CompanySummary
    initialization_job: JobSummary


class OnboardingStatusResponse(BaseModel):
    has_company_profile: bool
    onboarding_status: OnboardingStatus | None
    company_profile_id: str | None
    latest_job: JobSummary | None


def company_document(profile: CompanyProfile) -> dict[str, Any]:
    document = profile.model_dump(mode="python")
    document["_id"] = document.pop("id")
    return document


def model_from_document(model: type[BaseModel], document: dict[str, Any]) -> BaseModel:
    data = dict(document)
    data["id"] = str(data.pop("_id"))
    return model.model_validate(data)
