import asyncio
import importlib
import random
import re
from collections import deque
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from app.core.config import Settings
from app.crawlers.models import (
    CrawlError,
    CrawlErrorCode,
    CrawlPageResult,
    CrawlRequest,
    CrawlResult,
)
from app.crawlers.policy import CrawlPolicy, CrawlPolicyViolation


class Crawl4AIWebsiteCrawler:
    """Collect a bounded company website baseline through Crawl4AI."""

    def __init__(self, settings: Settings, policy: CrawlPolicy) -> None:
        self._settings = settings
        self._policy = policy

    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        started_at = datetime.now(UTC)
        registered_url = str(request.url)
        try:
            start_url = await self._policy.validate_target(registered_url, registered_url)
        except CrawlPolicyViolation as exc:
            return self._result(started_at, [], [self._policy_error(exc, registered_url)])

        pages: list[CrawlPageResult] = []
        errors: list[CrawlError] = []
        queue: deque[tuple[str, int]] = deque([(start_url, 0)])
        visited: set[str] = set()
        crawl4ai = importlib.import_module("crawl4ai")
        browser_config = crawl4ai.BrowserConfig(
            headless=True,
            user_agent=self._settings.crawler_user_agent,
            verbose=False,
            accept_downloads=False,
        )
        run_config = crawl4ai.CrawlerRunConfig(
            cache_mode=crawl4ai.CacheMode.BYPASS,
            check_robots_txt=(
                request.check_robots
                if request.check_robots is not None
                else self._settings.crawler_check_robots
            ),
            page_timeout=request.page_timeout_ms or self._settings.crawler_page_timeout_ms,
            semaphore_count=1,
            max_retries=0,
            screenshot=False,
            pdf=False,
            capture_mhtml=False,
            remove_consent_popups=True,
            exclude_external_links=False,
            verbose=False,
        )
        async with crawl4ai.AsyncWebCrawler(config=browser_config) as crawler:
            while queue and len(pages) < request.max_pages:
                requested_url, depth = queue.popleft()
                if requested_url.casefold() in visited:
                    continue
                visited.add(requested_url.casefold())
                if pages and self._settings.crawler_max_delay_seconds > 0:
                    await asyncio.sleep(
                        random.uniform(
                            self._settings.crawler_min_delay_seconds,
                            self._settings.crawler_max_delay_seconds,
                        )
                    )
                page, page_error = await self._crawl_page(
                    crawler, requested_url, registered_url, run_config, request
                )
                if page_error is not None:
                    errors.append(page_error)
                    continue
                if page is None:
                    continue
                pages.append(page)
                if depth >= request.max_depth:
                    continue
                remaining = request.max_pages - len(pages) - len(queue)
                if remaining <= 0:
                    continue
                for link in self._policy.select_links(
                    page.links, registered_url, max_pages=remaining
                ):
                    if link.casefold() not in visited:
                        queue.append((link, depth + 1))

        if not pages:
            errors.append(
                CrawlError(
                    code=CrawlErrorCode.CRAWL_NO_USABLE_CONTENT,
                    message="The company website produced no usable public content.",
                    url=start_url,
                )
            )
        return self._result(started_at, pages, errors)

    async def _crawl_page(
        self,
        crawler: Any,
        requested_url: str,
        registered_url: str,
        run_config: Any,
        request: CrawlRequest,
    ) -> tuple[CrawlPageResult | None, CrawlError | None]:
        try:
            safe_url = await self._policy.validate_target(requested_url, registered_url)
        except CrawlPolicyViolation as exc:
            return None, self._policy_error(exc, requested_url)
        attempts = (
            (request.max_retries or 0) + 1
            if request.max_retries is not None
            else (self._settings.crawler_max_retries + 1)
        )
        timeout_ms = request.page_timeout_ms or self._settings.crawler_page_timeout_ms
        max_response_bytes = request.max_response_bytes or self._settings.crawler_max_response_bytes
        for attempt in range(attempts):
            page_started = perf_counter()
            try:
                async with asyncio.timeout(timeout_ms / 1000):
                    container = await crawler.arun(url=safe_url, config=run_config)
                    raw = container[0]
            except TimeoutError:
                if attempt + 1 < attempts:
                    continue
                return None, CrawlError(
                    code=CrawlErrorCode.CRAWL_TIMEOUT,
                    message="The company website did not respond within the allowed time.",
                    url=safe_url,
                )
            except Exception:
                if attempt + 1 < attempts:
                    continue
                return None, CrawlError(
                    code=CrawlErrorCode.CRAWL_FAILED,
                    message="The company website could not be collected.",
                    url=safe_url,
                )

            error_message = (raw.error_message or "").casefold()
            status_code = raw.redirected_status_code or raw.status_code
            if "robots" in error_message:
                return None, CrawlError(
                    code=CrawlErrorCode.CRAWL_BLOCKED_BY_ROBOTS,
                    message="The company website disallows this crawl through robots.txt.",
                    url=safe_url,
                )
            if status_code == 429:
                if attempt + 1 < attempts:
                    await asyncio.sleep(self._retry_after(raw.response_headers))
                    continue
                return None, CrawlError(
                    code=CrawlErrorCode.CRAWL_RATE_LIMITED,
                    message="The company website rate-limited the crawl.",
                    url=safe_url,
                )
            if status_code == 403 or self._is_access_denied(error_message):
                return None, CrawlError(
                    code=CrawlErrorCode.CRAWL_ACCESS_DENIED,
                    message="The company website denied public crawl access.",
                    url=safe_url,
                )
            if status_code is not None and status_code >= 500 and attempt + 1 < attempts:
                continue
            if not raw.success:
                return None, CrawlError(
                    code=CrawlErrorCode.CRAWL_FAILED,
                    message="The company website page could not be collected.",
                    url=safe_url,
                )
            if len(raw.html.encode("utf-8")) > max_response_bytes:
                return None, CrawlError(
                    code=CrawlErrorCode.CRAWL_RESPONSE_TOO_LARGE,
                    message="The company website page exceeded the response-size limit.",
                    url=safe_url,
                )
            final_url = raw.redirected_url or raw.url or safe_url
            try:
                final_url = await self._policy.validate_redirect(final_url, registered_url)
            except CrawlPolicyViolation as exc:
                return None, self._policy_error(exc, final_url)
            markdown = self._markdown_text(raw.markdown)
            visible_text = self._visible_text(markdown)
            if not visible_text:
                return None, CrawlError(
                    code=CrawlErrorCode.CRAWL_NO_USABLE_CONTENT,
                    message="The company website page contained no usable text.",
                    url=safe_url,
                )
            metadata = self._metadata(raw.metadata)
            links = self._links(raw.links)
            content_type = str((raw.response_headers or {}).get("content-type", "")) or None
            return (
                CrawlPageResult(
                    requested_url=safe_url,
                    final_url=final_url,
                    status_code=status_code,
                    title=metadata.get("title"),
                    markdown=markdown,
                    visible_text=visible_text,
                    links=links,
                    metadata=metadata,
                    fetched_at=datetime.now(UTC),
                    crawler_used="crawl4ai",
                    content_type=content_type,
                    crawl_duration_ms=round((perf_counter() - page_started) * 1000, 2),
                ),
                None,
            )
        return None, CrawlError(
            code=CrawlErrorCode.CRAWL_FAILED,
            message="The company website page could not be collected.",
            url=safe_url,
        )

    def _retry_after(self, headers: dict[str, Any] | None) -> float:
        value = (headers or {}).get("retry-after", self._settings.crawler_min_delay_seconds)
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            seconds = self._settings.crawler_min_delay_seconds
        return min(max(seconds, self._settings.crawler_min_delay_seconds), 60.0)

    @staticmethod
    def _markdown_text(value: Any) -> str:
        raw_markdown = getattr(value, "raw_markdown", value)
        return str(raw_markdown or "")

    @staticmethod
    def _visible_text(markdown: str) -> str:
        text = re.sub(r"!?\[([^]]*)]\([^)]*\)", r"\1", markdown)
        text = re.sub(r"[`*_>#~-]+", " ", text)
        return " ".join(text.split())

    @staticmethod
    def _metadata(value: dict[str, Any] | None) -> dict[str, str]:
        return {
            str(key): str(item)
            for key, item in (value or {}).items()
            if isinstance(item, (str, int, float, bool)) and len(str(item)) <= 2000
        }

    @staticmethod
    def _links(value: dict[str, list[dict[str, Any]]]) -> list[str]:
        return [
            str(item["href"])
            for group in value.values()
            for item in group
            if isinstance(item, dict) and item.get("href")
        ]

    @staticmethod
    def _is_access_denied(error_message: str) -> bool:
        return any(
            marker in error_message
            for marker in ("captcha", "access denied", "forbidden", "cloudflare challenge")
        )

    @staticmethod
    def _policy_error(exc: CrawlPolicyViolation, url: str) -> CrawlError:
        return CrawlError(code=exc.code, message=exc.message, url=url)

    @staticmethod
    def _result(
        started_at: datetime, pages: list[CrawlPageResult], errors: list[CrawlError]
    ) -> CrawlResult:
        return CrawlResult(
            pages=pages,
            errors=errors,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
