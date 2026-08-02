import asyncio
import posixpath
import re
import socket
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from app.core.logging import get_logger
from app.crawlers.models import CrawlErrorCode
from app.domain.onboarding import normalize_website_url

logger = get_logger(__name__)

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

EXCLUDED_CRAWL_PATH_SEGMENTS = frozenset(
    {
        "account",
        "admin",
        "auth",
        "cart",
        "checkout",
        "dashboard",
        "forgot-password",
        "legal",
        "log-in",
        "login",
        "logout",
        "oauth",
        "privacy",
        "privacy-policy",
        "private",
        "profile",
        "register",
        "reset-password",
        "settings",
        "sign-in",
        "sign-up",
        "signin",
        "signup",
        "sso",
        "terms",
        "terms-of-service",
        "verify-email",
    }
)

EXCLUDED_CRAWL_FILE_SUFFIXES = (
    ".pdf",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".css",
    ".js",
    ".xml",
    ".json",
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
            normalized = self.normalize_candidate(url, registered_url)
        except ValueError as exc:
            raise CrawlPolicyViolation(
                CrawlErrorCode.INVALID_CRAWL_TARGET,
                "The crawl target is not a valid public HTTP(S) URL.",
            ) from exc
        if self.is_excluded_url(normalized):
            self._log_rejection(normalized, "excluded_path")
            raise CrawlPolicyViolation(
                CrawlErrorCode.INVALID_CRAWL_TARGET,
                "The crawl target path is excluded by policy.",
            )
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
                normalized = self.normalize_candidate(link, registered_url)
            except ValueError:
                continue
            if self.is_excluded_url(normalized):
                self._log_rejection(normalized, "excluded_path")
                continue
            if self.has_excluded_file_suffix(normalized):
                self._log_rejection(normalized, "excluded_file_route")
                continue
            if not self.is_same_company_domain(normalized, registered_url):
                continue
            unique.setdefault(normalized.casefold(), normalized)
        ordered = sorted(unique.values(), key=self._priority)
        return ordered[:max_pages]

    @classmethod
    def normalize_candidate(cls, value: str, registered_url: str) -> str:
        """Resolve a relative link and normalize its path for deterministic policy checks."""
        base = f"{registered_url.rstrip('/')}/"
        candidate = urljoin(base, value.strip())
        normalized = normalize_website_url(candidate)
        parsed = urlsplit(normalized)
        path = cls.normalized_path(normalized)
        rendered_path = "" if path == "/" else quote(path, safe="/:@-._~!$&'()*+,;=")
        return urlunsplit((parsed.scheme, parsed.netloc, rendered_path, parsed.query, ""))

    @staticmethod
    def normalized_path(value: str) -> str:
        """Decode and canonicalize a URL path without using substring matching."""
        path = urlsplit(value).path.replace("\\", "/")
        for _ in range(3):
            decoded = unquote(path)
            if decoded == path:
                break
            path = decoded
        path = re.sub(r"/+", "/", path)
        normalized = posixpath.normpath(path or "/")
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return "/" if normalized == "/." else normalized.rstrip("/") or "/"

    @classmethod
    def is_excluded_url(cls, value: str) -> bool:
        segments = {
            segment.casefold()
            for segment in cls.normalized_path(value).split("/")
            if segment and segment not in {".", ".."}
        }
        return bool(segments & EXCLUDED_CRAWL_PATH_SEGMENTS)

    @classmethod
    def has_excluded_file_suffix(cls, value: str) -> bool:
        return cls.normalized_path(value).casefold().endswith(EXCLUDED_CRAWL_FILE_SUFFIXES)

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

    @classmethod
    def _log_rejection(cls, value: str, category: str) -> None:
        parsed = urlsplit(value)
        logger.info(
            "crawl_url_rejected",
            rejection_category=category,
            hostname=parsed.hostname,
            path=cls.normalized_path(value),
        )

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
