from __future__ import annotations

import re
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.crawlers.models import CrawlRequest, WebsiteCrawler
from app.crawlers.policy import CrawlPolicy
from app.domain.discovery import Competitor
from app.domain.intelligence import PageType
from app.domain.monitoring import (
    CompetitorSource,
    CompetitorSourceCreate,
    CompetitorSourcePage,
    CompetitorSourceType,
    CompetitorSourceUpdate,
    DiscoveredSource,
    SourcePriority,
    SourceStatus,
    canonicalize_source_url,
)
from app.domain.onboarding import new_id, utc_now
from app.repositories.interfaces import Persistence, RepositoryConflictError
from app.services.normalization import ContentNormalizer

COMMON_SOURCE_PATHS = (
    "/pricing",
    "/product",
    "/products",
    "/features",
    "/solutions",
    "/docs",
    "/documentation",
    "/changelog",
    "/releases",
    "/blog",
    "/customers",
    "/case-studies",
    "/integrations",
    "/about",
)
PUBLIC_URL = re.compile(r"https?://[^\s<>'\")\]]+", re.IGNORECASE)


class CompetitorSourceDiscoveryService(Protocol):
    async def discover(
        self, *, organization_id: str, competitor_id: str
    ) -> list[DiscoveredSource]: ...


class DeterministicCompetitorSourceDiscoveryService:
    """Discover a bounded, same-domain source set from one public homepage crawl."""

    def __init__(
        self,
        persistence: Persistence,
        crawler: WebsiteCrawler,
        policy: CrawlPolicy,
        settings: Settings,
    ) -> None:
        self._persistence = persistence
        self._crawler = crawler
        self._policy = policy
        self._settings = settings

    async def discover(self, *, organization_id: str, competitor_id: str) -> list[DiscoveredSource]:
        competitor = await self._persistence.competitors.get_by_id(organization_id, competitor_id)
        if competitor is None:
            return []
        result = await self._crawler.crawl(
            CrawlRequest(
                url=competitor.canonical_url,
                organization_id=organization_id,
                company_profile_id=competitor_id,
                source_id=f"discovery:{competitor_id}",
                max_depth=0,
                max_pages=1,
            )
        )
        candidates: list[tuple[str, str, list[str]]] = [
            (competitor.canonical_url, "approved_candidate", [competitor.canonical_url])
        ]
        metadata_urls = [
            urljoin(f"{competitor.canonical_url.rstrip('/')}/", "sitemap.xml"),
            urljoin(f"{competitor.canonical_url.rstrip('/')}/", "robots.txt"),
        ]
        for page in result.pages:
            candidates.extend(
                (link, "homepage_link", [competitor.canonical_url]) for link in page.links
            )
            metadata_urls.extend(link for link in page.links if "sitemap" in link.casefold())
        for metadata_url in list(dict.fromkeys(metadata_urls))[:3]:
            try:
                metadata_result = await self._crawler.crawl(
                    CrawlRequest(
                        url=metadata_url,
                        organization_id=organization_id,
                        company_profile_id=competitor_id,
                        source_id=f"discovery-metadata:{competitor_id}",
                        max_depth=0,
                        max_pages=1,
                        max_retries=self._settings.competitor_crawler_max_retries,
                        page_timeout_ms=self._settings.competitor_crawler_page_timeout_ms,
                        max_response_bytes=self._settings.competitor_crawler_max_response_bytes,
                        check_robots=self._settings.competitor_crawler_check_robots,
                    )
                )
            except Exception:
                continue
            for page in metadata_result.pages:
                referenced = [*page.links, *PUBLIC_URL.findall(page.markdown)]
                candidates.extend((link, "sitemap", [metadata_url]) for link in referenced)
        candidates.extend(
            (
                urljoin(f"{competitor.canonical_url.rstrip('/')}/", path.lstrip("/")),
                "common_path",
                [path],
            )
            for path in COMMON_SOURCE_PATHS
        )
        evidence_by_url: dict[str, tuple[str, list[str]]] = {}
        for url, method, evidence in candidates:
            try:
                canonical = canonicalize_source_url(url)
            except ValueError:
                continue
            evidence_by_url.setdefault(canonical.casefold(), (method, evidence))
        selected = self._policy.select_links(
            [item[0] for item in candidates],
            competitor.canonical_url,
            max_pages=self._settings.competitor_crawler_max_sources * 4,
        )
        selected.sort(
            key=lambda value: (
                evidence_by_url.get(canonicalize_source_url(value).casefold(), ("common_path", []))[
                    0
                ]
                == "common_path"
            )
        )
        discovered: list[DiscoveredSource] = []
        seen: set[str] = set()
        for value in selected:
            try:
                canonical = canonicalize_source_url(value)
            except ValueError:
                continue
            path = urlsplit(canonical).path.casefold()
            if self._policy.is_excluded_url(canonical) or self._policy.has_excluded_file_suffix(
                canonical
            ):
                continue
            if canonical.casefold() in seen:
                continue
            source_type = source_type_for_page(ContentNormalizer.page_type(canonical))
            if source_type == CompetitorSourceType.WEBSITE and path not in {"", "/"}:
                continue
            method, evidence = evidence_by_url.get(
                canonical.casefold(), ("homepage_link", [competitor.canonical_url])
            )
            if method != "approved_candidate":
                try:
                    validation = await self._crawler.crawl(
                        CrawlRequest(
                            url=canonical,
                            organization_id=organization_id,
                            company_profile_id=competitor_id,
                            source_id=f"discovery-validation:{competitor_id}",
                            max_depth=0,
                            max_pages=1,
                            max_retries=0,
                            page_timeout_ms=self._settings.competitor_crawler_page_timeout_ms,
                            max_response_bytes=self._settings.competitor_crawler_max_response_bytes,
                            check_robots=self._settings.competitor_crawler_check_robots,
                        )
                    )
                except Exception:
                    continue
                if not validation.pages:
                    continue
            seen.add(canonical.casefold())
            discovered.append(
                DiscoveredSource(
                    url=canonical,
                    page_type=source_type,
                    priority=priority_for_type(source_type),
                    discovery_method=method,
                    evidence=evidence,
                )
            )
            if len(discovered) >= self._settings.competitor_crawler_max_sources:
                break
        return discovered


class CompetitorSourceService:
    def __init__(self, persistence: Persistence) -> None:
        self._persistence = persistence

    async def get_competitor(self, context: AuthenticatedContext, competitor_id: str) -> Competitor:
        competitor = await self._persistence.competitors.get_by_id(
            context.organization_id, competitor_id
        )
        if competitor is None:
            raise AppError(
                code="COMPETITOR_NOT_FOUND",
                message="The competitor was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return competitor

    async def list_sources(
        self,
        context: AuthenticatedContext,
        competitor_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> CompetitorSourcePage:
        await self.get_competitor(context, competitor_id)
        items = await self._persistence.competitor_sources.list_for_competitor(
            context.organization_id,
            competitor_id,
            cursor=cursor,
            limit=limit + 1,
        )
        return CompetitorSourcePage(
            items=items[:limit],
            next_cursor=items[limit - 1].id if len(items) > limit else None,
        )

    async def create(
        self,
        context: AuthenticatedContext,
        competitor_id: str,
        request: CompetitorSourceCreate,
        *,
        discovered_automatically: bool = False,
        discovery_method: str | None = None,
        discovery_evidence: list[str] | None = None,
    ) -> CompetitorSource:
        competitor = await self.get_competitor(context, competitor_id)
        try:
            canonical = canonicalize_source_url(request.url)
        except ValueError as exc:
            raise AppError(
                code="INVALID_COMPETITOR_SOURCE_URL",
                message="The source must be a safe public HTTP(S) URL without credentials.",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc
        if not CrawlPolicy.is_same_company_domain(canonical, competitor.canonical_url):
            raise AppError(
                code="COMPETITOR_SOURCE_DOMAIN_MISMATCH",
                message="The source must belong to the competitor's approved domain.",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        page_type = ContentNormalizer.page_type(canonical)
        source_type = request.type or source_type_for_page(page_type)
        now = utc_now()
        source = CompetitorSource(
            id=new_id(),
            organization_id=context.organization_id,
            competitor_id=competitor_id,
            type=source_type,
            url=canonical,
            canonical_url=canonical,
            canonical_domain=urlsplit(canonical).hostname or "",
            priority=request.priority,
            discovered_automatically=discovered_automatically,
            page_type=page_type,
            discovery_method=discovery_method,
            discovery_evidence=discovery_evidence or [],
            created_at=now,
            updated_at=now,
        )
        try:
            created = await self._persistence.competitor_sources.create(source)
        except RepositoryConflictError as exc:
            raise AppError(
                code="COMPETITOR_SOURCE_ALREADY_EXISTS",
                message="This competitor source already exists.",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        enabled = await self._persistence.competitor_sources.list_for_competitor(
            context.organization_id, competitor_id, enabled_only=True
        )
        await self._persistence.competitors.update(
            context.organization_id, competitor_id, {"source_count": len(enabled)}
        )
        return created

    async def update(
        self,
        context: AuthenticatedContext,
        competitor_id: str,
        source_id: str,
        request: CompetitorSourceUpdate,
    ) -> CompetitorSource:
        await self.get_competitor(context, competitor_id)
        changes = request.model_dump(exclude_none=True)
        if changes.get("enabled") is False:
            changes["status"] = SourceStatus.DISABLED
        elif changes.get("enabled") is True:
            changes["status"] = SourceStatus.PENDING
        updated = await self._persistence.competitor_sources.update(
            context.organization_id, competitor_id, source_id, changes
        )
        if updated is None:
            raise self._not_found()
        enabled = await self._persistence.competitor_sources.list_for_competitor(
            context.organization_id, competitor_id, enabled_only=True
        )
        await self._persistence.competitors.update(
            context.organization_id, competitor_id, {"source_count": len(enabled)}
        )
        return updated

    async def disable(
        self, context: AuthenticatedContext, competitor_id: str, source_id: str
    ) -> CompetitorSource:
        return await self.update(
            context,
            competitor_id,
            source_id,
            CompetitorSourceUpdate(enabled=False),
        )

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            code="COMPETITOR_SOURCE_NOT_FOUND",
            message="The competitor source was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


def source_type_for_page(page_type: PageType) -> CompetitorSourceType:
    try:
        return CompetitorSourceType(page_type.value)
    except ValueError:
        return CompetitorSourceType.WEBSITE


def priority_for_type(source_type: CompetitorSourceType) -> SourcePriority:
    if source_type in {
        CompetitorSourceType.WEBSITE,
        CompetitorSourceType.PRICING,
        CompetitorSourceType.PRODUCT,
        CompetitorSourceType.FEATURES,
        CompetitorSourceType.CHANGELOG,
    }:
        return SourcePriority.HIGH
    if source_type in {
        CompetitorSourceType.SOLUTIONS,
        CompetitorSourceType.DOCUMENTATION,
        CompetitorSourceType.INTEGRATIONS,
    }:
        return SourcePriority.MEDIUM
    return SourcePriority.LOW
