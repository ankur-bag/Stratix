import re
from collections import Counter
from typing import Protocol

from app.core.config import Settings
from app.domain.intelligence import NormalizedSection
from app.domain.scanning import (
    ChangeSet,
    CompetitorSnapshot,
    IgnoredChange,
    MetadataChange,
    ModifiedSection,
    NumericChange,
    SectionChange,
)


class ChangeDetector(Protocol):
    """Compare two stored snapshots without model inference."""

    def compare(
        self, *, previous: CompetitorSnapshot, current: CompetitorSnapshot
    ) -> ChangeSet: ...


_NOISE_HEADINGS = re.compile(
    r"^(navigation|menu|footer|legal|cookie(?: preferences)?|privacy|terms)$", re.I
)
_TOKEN = re.compile(r"[\w%$€£¥]+", re.UNICODE)
_PRICE = re.compile(
    r"(?:(?P<symbol>[$€£¥])\s*(?P<amount>\d+(?:[.,]\d{1,2})?)|"
    r"(?P<amount_after>\d+(?:[.,]\d{1,2})?)\s*(?P<code>USD|EUR|GBP|JPY))",
    re.I,
)
_CURRENCIES = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}


class DeterministicChangeDetector:
    """Match stable sections, extract numeric changes, and suppress common noise."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def compare(self, *, previous: CompetitorSnapshot, current: CompetitorSnapshot) -> ChangeSet:
        if previous.content_hash == current.content_hash:
            return ChangeSet(
                changed=False,
                significance_score=0,
                ignored_changes=[
                    IgnoredChange(reason="identical_hash", detail="Content is unchanged.")
                ],
            )

        old, old_ignored = self._indexed(previous.sections)
        new, new_ignored = self._indexed(current.sections)
        ignored = [*old_ignored, *new_ignored]
        added: list[SectionChange] = []
        removed: list[SectionChange] = []
        modified: list[ModifiedSection] = []

        for identifier in sorted(old.keys() | new.keys()):
            before = old.get(identifier)
            after = new.get(identifier)
            if before is None and after is not None:
                added.append(self._section_change(identifier, after))
            elif after is None and before is not None:
                removed.append(self._section_change(identifier, before))
            elif before is not None and after is not None:
                if before[0].content_hash == after[0].content_hash:
                    if before[1] != after[1]:
                        ignored.append(
                            IgnoredChange(
                                reason="section_moved",
                                detail=before[0].heading or identifier,
                            )
                        )
                    continue
                if self._semantic_text(before[0]) == self._semantic_text(after[0]):
                    ignored.append(
                        IgnoredChange(reason="punctuation_or_whitespace", detail=identifier)
                    )
                    continue
                modified.append(
                    ModifiedSection(
                        identifier=identifier,
                        heading=after[0].heading or before[0].heading,
                        before=before[0].content,
                        after=after[0].content,
                        before_position=before[1],
                        after_position=after[1],
                    )
                )

        added, removed, renamed = self._match_renamed_sections(added, removed)
        numeric = [
            *self._numeric_changes(previous.normalized_text, current.normalized_text),
            *renamed,
        ]
        metadata = self._metadata_changes(previous, current)
        changed_length = sum(len(item.content) for item in added + removed) + sum(
            max(len(item.before), len(item.after)) for item in modified
        )
        total_length = max(len(previous.normalized_text), len(current.normalized_text), 1)
        score = min(changed_length / total_length, 1.0)
        if numeric:
            score = max(score, 0.8)
        elif added or removed:
            score = max(score, 0.45)
        elif modified:
            score = max(score, 0.3)
        meaningful_length = sum(
            len(value)
            for value in [
                *(item.content for item in added),
                *(item.content for item in removed),
                *(item.after for item in modified),
            ]
        )
        changed = bool(
            (added or removed or modified or numeric or metadata)
            and score >= self._settings.change_min_significance_score
            and (meaningful_length >= self._settings.change_min_text_length or numeric)
        )
        if not changed:
            ignored.append(
                IgnoredChange(reason="below_significance_threshold", detail=f"score={score:.4f}")
            )
        return ChangeSet(
            changed=changed,
            significance_score=round(score, 4),
            added_sections=added,
            removed_sections=removed,
            modified_sections=modified,
            numeric_changes=numeric,
            metadata_changes=metadata,
            ignored_changes=ignored,
        )

    def _indexed(
        self, sections: list[NormalizedSection]
    ) -> tuple[dict[str, tuple[NormalizedSection, int]], list[IgnoredChange]]:
        indexed: dict[str, tuple[NormalizedSection, int]] = {}
        ignored: list[IgnoredChange] = []
        counts: Counter[str] = Counter()
        for position, section in enumerate(sections):
            heading = (section.heading or "").strip()
            if self._is_noise(heading, section.content):
                ignored.append(
                    IgnoredChange(
                        reason="boilerplate_section",
                        detail=heading or f"section-{position}",
                    )
                )
                continue
            base = self._slug(heading) or f"section-{position}"
            counts[base] += 1
            identifier = base if counts[base] == 1 else f"{base}-{counts[base]}"
            indexed[identifier] = (section, position)
        return indexed, ignored

    def _is_noise(self, heading: str, content: str) -> bool:
        text = f"{heading} {content}".casefold()
        if _NOISE_HEADINGS.fullmatch(heading.strip()):
            return True
        return (
            self._settings.change_ignore_cookie_banners
            and "cookie" in text
            and any(marker in text for marker in ("consent", "preference", "accept all", "manage"))
        )

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")

    @staticmethod
    def _semantic_text(section: NormalizedSection) -> tuple[str, ...]:
        text = f"{section.heading or ''} {section.content}"
        return tuple(token.casefold() for token in _TOKEN.findall(text))

    @staticmethod
    def _section_change(identifier: str, value: tuple[NormalizedSection, int]) -> SectionChange:
        section, position = value
        return SectionChange(
            identifier=identifier,
            heading=section.heading,
            content=section.content,
            position=position,
        )

    @staticmethod
    def _match_renamed_sections(
        added: list[SectionChange], removed: list[SectionChange]
    ) -> tuple[list[SectionChange], list[SectionChange], list[NumericChange]]:
        renamed: list[NumericChange] = []
        matched_added: set[int] = set()
        matched_removed: set[int] = set()
        for old_index, old in enumerate(removed):
            for new_index, new in enumerate(added):
                if new_index in matched_added:
                    continue
                if old.content and old.content == new.content and old.heading != new.heading:
                    renamed.append(
                        NumericChange(
                            field="Plan name",
                            before=old.heading,
                            after=new.heading,
                            change_type="plan_renamed",
                        )
                    )
                    matched_added.add(new_index)
                    matched_removed.add(old_index)
                    break
        return (
            [item for index, item in enumerate(added) if index not in matched_added],
            [item for index, item in enumerate(removed) if index not in matched_removed],
            renamed,
        )

    @staticmethod
    def _prices(text: str) -> list[tuple[str, str]]:
        values: list[tuple[str, str]] = []
        for match in _PRICE.finditer(text):
            amount = (match.group("amount") or match.group("amount_after") or "").replace(",", ".")
            currency = match.group("code") or _CURRENCIES.get(match.group("symbol") or "", "")
            values.append((amount, currency.upper()))
        return values

    def _numeric_changes(self, before: str, after: str) -> list[NumericChange]:
        old_prices = self._prices(before)
        new_prices = self._prices(after)
        changes: list[NumericChange] = []
        for index in range(max(len(old_prices), len(new_prices))):
            old = old_prices[index] if index < len(old_prices) else None
            new = new_prices[index] if index < len(new_prices) else None
            if old == new:
                continue
            if old is None and new is not None:
                change_type = "price_added"
            elif new is None and old is not None:
                change_type = "price_removed"
            elif old is not None and new is not None and old[1] != new[1]:
                change_type = "currency_changed"
            else:
                assert old is not None and new is not None
                try:
                    change_type = (
                        "increase"
                        if float(new[0]) > float(old[0])
                        else "decrease"
                        if float(new[0]) < float(old[0])
                        else "changed"
                    )
                except (TypeError, ValueError):
                    change_type = "changed"
            changes.append(
                NumericChange(
                    field=f"Published price {index + 1}",
                    before=old[0] if old else None,
                    after=new[0] if new else None,
                    currency=(new or old or ("", None))[1],
                    change_type=change_type,
                )
            )
        old_free = bool(re.search(r"\bfree (?:tier|plan)\b", before, re.I))
        new_free = bool(re.search(r"\bfree (?:tier|plan)\b", after, re.I))
        if old_free != new_free:
            changes.append(
                NumericChange(
                    field="Free tier",
                    before="available" if old_free else "not observed",
                    after="available" if new_free else "not observed",
                    change_type="free_tier_added" if new_free else "free_tier_removed",
                )
            )
        return changes

    @staticmethod
    def _metadata_changes(
        previous: CompetitorSnapshot, current: CompetitorSnapshot
    ) -> list[MetadataChange]:
        changes: list[MetadataChange] = []
        if previous.title != current.title:
            changes.append(
                MetadataChange(field="title", before=previous.title, after=current.title)
            )
        return changes
