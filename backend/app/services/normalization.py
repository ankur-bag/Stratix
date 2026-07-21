import hashlib
import re
from urllib.parse import urlsplit

from app.domain.intelligence import (
    NormalizedDocument,
    NormalizedSection,
    PageType,
)

BOILERPLATE_EXACT = {
    "accept cookies",
    "cookie settings",
    "privacy preferences",
    "skip to content",
    "all rights reserved",
    "back to top",
}


class ContentNormalizer:
    """Create deterministic, meaningful page sections without model calls."""

    def normalize(self, markdown: str, visible_text: str = "") -> NormalizedDocument:
        source = markdown if markdown.strip() else visible_text
        sections: list[tuple[str | None, list[str]]] = []
        heading: str | None = None
        content: list[str] = []
        previous: str | None = None
        for raw_line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = self._normalize_line(raw_line)
            if not line or self._is_boilerplate(line) or line == previous:
                continue
            previous = line
            match = re.match(r"^#{1,6}\s+(.+)$", line)
            if match:
                self._append_section(sections, heading, content)
                heading = self._plain_text(match.group(1))
                content = []
            else:
                plain = self._plain_text(line)
                if plain:
                    content.append(plain)
        self._append_section(sections, heading, content)
        if not sections:
            return NormalizedDocument(
                markdown="", text="", content_hash=self._hash(""), sections=[]
            )

        normalized_sections = [
            self._section(item_heading, lines) for item_heading, lines in sections
        ]
        normalized_markdown = "\n\n".join(
            self._section_markdown(section) for section in normalized_sections
        )
        normalized_text = "\n\n".join(
            "\n".join(part for part in (section.heading, section.content) if part)
            for section in normalized_sections
        )
        return NormalizedDocument(
            markdown=normalized_markdown,
            text=normalized_text,
            content_hash=self._hash(normalized_text),
            sections=normalized_sections,
        )

    @staticmethod
    def page_type(url: str) -> PageType:
        path = urlsplit(url).path.casefold().strip("/")
        if not path:
            return PageType.HOMEPAGE
        terms = (
            ("pricing", PageType.PRICING),
            ("feature", PageType.FEATURES),
            ("solution", PageType.SOLUTIONS),
            ("product", PageType.PRODUCT),
            ("about", PageType.ABOUT),
            ("docs", PageType.DOCUMENTATION),
            ("documentation", PageType.DOCUMENTATION),
            ("changelog", PageType.CHANGELOG),
            ("blog", PageType.BLOG),
            ("customer", PageType.CUSTOMERS),
            ("integration", PageType.INTEGRATIONS),
        )
        return next((page_type for term, page_type in terms if term in path), PageType.OTHER)

    @staticmethod
    def _normalize_line(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip())

    @staticmethod
    def _plain_text(value: str) -> str:
        value = re.sub(r"!?\[([^]]*)]\([^)]*\)", r"\1", value)
        value = re.sub(r"<[^>]+>", " ", value)
        value = re.sub(r"[`*_~]+", "", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _is_boilerplate(value: str) -> bool:
        lowered = ContentNormalizer._plain_text(value).casefold()
        if lowered in BOILERPLATE_EXACT:
            return True
        return "cookie" in lowered and any(
            marker in lowered for marker in ("consent", "accept", "preference", "manage")
        )

    @staticmethod
    def _append_section(
        sections: list[tuple[str | None, list[str]]],
        heading: str | None,
        content: list[str],
    ) -> None:
        if heading or content:
            sections.append((heading, list(content)))

    def _section(self, heading: str | None, lines: list[str]) -> NormalizedSection:
        content = "\n".join(lines)
        canonical = "\n".join(part for part in (heading, content) if part)
        return NormalizedSection(
            heading=heading,
            content=content,
            content_hash=self._hash(canonical),
        )

    @staticmethod
    def _section_markdown(section: NormalizedSection) -> str:
        heading = f"## {section.heading}" if section.heading else ""
        return "\n".join(part for part in (heading, section.content) if part)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
