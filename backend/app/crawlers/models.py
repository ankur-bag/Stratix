from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field, HttpUrl


class CrawlErrorCode(StrEnum):
    INVALID_CRAWL_TARGET = "INVALID_CRAWL_TARGET"
    CRAWL_BLOCKED_BY_ROBOTS = "CRAWL_BLOCKED_BY_ROBOTS"
    CRAWL_ACCESS_DENIED = "CRAWL_ACCESS_DENIED"
    CRAWL_RATE_LIMITED = "CRAWL_RATE_LIMITED"
    CRAWL_TIMEOUT = "CRAWL_TIMEOUT"
    CRAWL_NO_USABLE_CONTENT = "CRAWL_NO_USABLE_CONTENT"
    CRAWL_RESPONSE_TOO_LARGE = "CRAWL_RESPONSE_TOO_LARGE"
    CRAWL_FAILED = "CRAWL_FAILED"


class CrawlRequest(BaseModel):
    url: HttpUrl
    organization_id: str
    company_profile_id: str
    source_id: str
    max_depth: int = Field(ge=0, le=5)
    max_pages: int = Field(ge=1, le=100)


class CrawlPageResult(BaseModel):
    requested_url: str
    final_url: str
    status_code: int | None
    title: str | None
    markdown: str
    visible_text: str
    links: list[str]
    metadata: dict[str, str]
    fetched_at: datetime
    crawler_used: str


class CrawlError(BaseModel):
    code: CrawlErrorCode
    message: str
    url: str | None = None
    retryable: bool = False


class CrawlResult(BaseModel):
    pages: list[CrawlPageResult]
    errors: list[CrawlError]
    started_at: datetime
    completed_at: datetime


class WebsiteCrawler(Protocol):
    async def crawl(self, request: CrawlRequest) -> CrawlResult: ...
