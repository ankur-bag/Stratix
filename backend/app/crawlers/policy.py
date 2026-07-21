import asyncio
import socket
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlsplit

from app.crawlers.models import CrawlErrorCode
from app.domain.onboarding import normalize_website_url

PRIORITY_PATH_TERMS = (
    "pricing",
    "product",
    "features",
    "solutions",
    "about",
    "docs",
    "documentation",
    "changelog",
    "blog",
    "customers",
    "integrations",
)


class DNSResolver(Protocol):
    async def resolve(self, hostname: str) -> list[str]: ...


class SystemDNSResolver:
    """Resolve crawl targets immediately before access to prevent DNS-based SSRF."""

    async def resolve(self, hostname: str) -> list[str]:
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        return sorted({str(record[4][0]) for record in records})


class CrawlPolicyViolation(Exception):
    def __init__(self, code: CrawlErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CrawlPolicy:
    """Validate crawl targets, redirects, and bounded same-domain discovery."""

    def __init__(self, resolver: DNSResolver) -> None:
        self._resolver = resolver

    async def validate_target(self, url: str, registered_url: str) -> str:
        try:
            normalized = normalize_website_url(url)
        except ValueError as exc:
            raise CrawlPolicyViolation(
                CrawlErrorCode.INVALID_CRAWL_TARGET,
                "The crawl target is not a valid public HTTP(S) URL.",
            ) from exc
        if not self.is_same_company_domain(normalized, registered_url):
            raise CrawlPolicyViolation(
                CrawlErrorCode.INVALID_CRAWL_TARGET,
                "The crawl target is outside the registered company domain.",
            )
        hostname = urlsplit(normalized).hostname
        if hostname is None:
            raise CrawlPolicyViolation(
                CrawlErrorCode.INVALID_CRAWL_TARGET, "The crawl target has no hostname."
            )
        try:
            addresses = await self._resolver.resolve(hostname)
        except (OSError, socket.gaierror) as exc:
            raise CrawlPolicyViolation(
                CrawlErrorCode.INVALID_CRAWL_TARGET,
                "The crawl target could not be resolved safely.",
            ) from exc
        if not addresses or any(not self._is_public_address(address) for address in addresses):
            raise CrawlPolicyViolation(
                CrawlErrorCode.INVALID_CRAWL_TARGET,
                "The crawl target resolved to a non-public destination.",
            )
        return normalized

    async def validate_redirect(self, url: str, registered_url: str) -> str:
        """Apply the complete target policy to a redirect destination."""
        return await self.validate_target(url, registered_url)

    def select_links(self, links: list[str], registered_url: str, *, max_pages: int) -> list[str]:
        unique: dict[str, str] = {}
        for link in links:
            try:
                normalized = normalize_website_url(link)
            except ValueError:
                continue
            if self.is_same_company_domain(normalized, registered_url):
                unique.setdefault(normalized.casefold(), normalized)
        ordered = sorted(unique.values(), key=self._priority)
        return ordered[:max_pages]

    @staticmethod
    def is_same_company_domain(url: str, registered_url: str) -> bool:
        candidate = urlsplit(url).hostname
        registered = urlsplit(registered_url).hostname
        if candidate is None or registered is None:
            return False
        candidate = candidate.rstrip(".").lower()
        registered = registered.rstrip(".").lower()
        root = registered.removeprefix("www.")
        return candidate in (registered, root) or candidate.endswith(f".{root}")

    @staticmethod
    def _priority(url: str) -> tuple[int, int, str]:
        path = urlsplit(url).path.casefold()
        if path in {"", "/"}:
            return (0, 0, url)
        rank = next(
            (index + 1 for index, term in enumerate(PRIORITY_PATH_TERMS) if term in path),
            len(PRIORITY_PATH_TERMS) + 1,
        )
        return (rank, path.count("/"), url)

    @staticmethod
    def _is_public_address(value: str) -> bool:
        try:
            address = ip_address(value)
        except ValueError:
            return False
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )
