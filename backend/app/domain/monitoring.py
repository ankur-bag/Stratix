from datetime import datetime
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field, model_validator

from app.domain.intelligence import PageType
from app.domain.onboarding import normalize_website_url


class CompetitorSourceType(StrEnum):
    WEBSITE = "website"
    PRICING = "pricing"
    PRODUCT = "product"
    FEATURES = "features"
    SOLUTIONS = "solutions"
    DOCUMENTATION = "documentation"
    CHANGELOG = "changelog"
    BLOG = "blog"
    CUSTOMERS = "customers"
    INTEGRATIONS = "integrations"
    ABOUT = "about"


class SourceStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    DISABLED = "disabled"
    BLOCKED = "blocked"
    FAILED = "failed"


class SourcePriority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def canonicalize_source_url(value: str) -> str:
    normalized = normalize_website_url(value)
    parsed = urlsplit(normalized)
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=False)
            if key.casefold() not in TRACKING_QUERY_KEYS
            and not key.casefold().startswith(TRACKING_QUERY_PREFIXES)
        )
    )
    path = parsed.path or ""
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), query, ""))


class CompetitorSource(BaseModel):
    id: str
    organization_id: str
    competitor_id: str
    type: CompetitorSourceType = CompetitorSourceType.WEBSITE
    url: str
    canonical_url: str = ""
    canonical_domain: str = ""
    enabled: bool = True
    status: SourceStatus = SourceStatus.PENDING
    priority: SourcePriority = SourcePriority.HIGH
    discovered_automatically: bool = False
    page_type: PageType = PageType.HOMEPAGE
    discovery_method: str | None = None
    discovery_evidence: list[str] = Field(default_factory=list)
    last_scanned_at: datetime | None = None
    last_successful_scan_at: datetime | None = None
    failure_count: int = Field(default=0, ge=0)
    last_error_code: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def fill_canonical_fields(self) -> "CompetitorSource":
        canonical = canonicalize_source_url(self.canonical_url or self.url)
        domain = urlsplit(canonical).hostname or ""
        object.__setattr__(self, "url", canonical)
        object.__setattr__(self, "canonical_url", canonical)
        object.__setattr__(self, "canonical_domain", domain)
        return self


class CompetitorSourceCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    type: CompetitorSourceType | None = None
    priority: SourcePriority = SourcePriority.MEDIUM


class CompetitorSourceUpdate(BaseModel):
    enabled: bool | None = None
    priority: SourcePriority | None = None
    type: CompetitorSourceType | None = None


class CompetitorSourcePage(BaseModel):
    items: list[CompetitorSource]
    next_cursor: str | None = None


class DiscoveredSource(BaseModel):
    url: str
    page_type: CompetitorSourceType
    priority: SourcePriority
    discovery_method: str
    evidence: list[str] = Field(default_factory=list)


class SourceDiscoveryResponse(BaseModel):
    scan_id: str
    status: str
