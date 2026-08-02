import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
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
_TOKEN = re.compile(r"[\w%$\u20ac\u00a3\u00a5]+", re.UNICODE)
_PRICE_OFFER = re.compile(
    r"(?:(?P<symbol>[$\u20ac\u00a3\u00a5])\s*(?P<amount>\d+(?:[.,]\d{1,2})?)|"
    r"(?P<amount_code>\d+(?:[.,]\d{1,2})?)\s*(?P<code>USD|EUR|GBP|JPY))"
    r"(?:\s*(?:/|per\s+)(?P<period>month|monthly|mo|year|yearly|annual|annually|yr))?",
    re.I,
)
_ZERO_PERIOD = re.compile(
    r"(?<![\w$\u20ac\u00a3\u00a5])0(?:[.,]0+)?\s*(?:/|per\s+)"
    r"(?P<period>month|monthly|mo|year|yearly|annual|annually|yr)\b",
    re.I,
)
_PLAN_SENTENCE = re.compile(
    r"^(?P<name>[A-Za-z][\w &+.'-]{0,60}?(?:\s+(?:plan|tier))?)\s+"
    r"(?:plan\s+)?(?:is|costs?|starts?\s+at|from)\s+(?P<offer>.+)$",
    re.I,
)
_LABELLED_ROW = re.compile(
    r"^(?P<name>[^:|\-\u2013\u2014]{1,80}?)\s+(?:-|\u2013|\u2014|:)\s+(?P<offer>.+)$"
)
_GLOBAL_FREE_PLAN = re.compile(r"\bfree\s+(?:plan|tier)\b", re.I)
_CONTACT_SALES = re.compile(r"\b(?:contact|talk to)\s+sales\b", re.I)
_CURRENCIES = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP", "\u00a5": "JPY"}
_GENERIC_PLAN_HEADINGS = {
    "choose a plan",
    "compare plans",
    "plans",
    "pricing",
    "pricing plans",
    "subscriptions",
}
_PERIODS = {
    "month": "monthly",
    "monthly": "monthly",
    "mo": "monthly",
    "year": "annual",
    "yearly": "annual",
    "annual": "annual",
    "annually": "annual",
    "yr": "annual",
}


@dataclass(frozen=True)
class PlanOffer:
    currency: str
    billing_period: str | None
    amount: str


@dataclass
class PricingPlan:
    name: str
    identity: str
    source_section: str
    confidence: float
    offers: set[PlanOffer] = field(default_factory=set)
    free: bool = False
    contact_sales: bool = False


class DeterministicChangeDetector:
    """Match stable sections and compare prices by stable plan identity."""

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
        numeric, pricing_ignored = self._pricing_changes(previous, current)
        ignored.extend(pricing_ignored)
        for item in renamed:
            if not any(
                change.change_type == "plan_renamed"
                and self._plan_identity(change.before or "")
                == self._plan_identity(item.before or "")
                and self._plan_identity(change.after or "") == self._plan_identity(item.after or "")
                for change in numeric
            ):
                numeric.append(item)
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

    def _pricing_changes(
        self, previous: CompetitorSnapshot, current: CompetitorSnapshot
    ) -> tuple[list[NumericChange], list[IgnoredChange]]:
        old, old_ignored, old_ambiguous = self._parse_pricing_plans(previous.sections)
        new, new_ignored, new_ambiguous = self._parse_pricing_plans(current.sections)
        ignored = [*old_ignored, *new_ignored]
        changes: list[NumericChange] = []
        for identity in old_ambiguous | new_ambiguous:
            old.pop(identity, None)
            new.pop(identity, None)

        common = old.keys() & new.keys()
        for identity in sorted(common):
            changes.extend(self._compare_plan(old[identity], new[identity]))

        old_only = {key: value for key, value in old.items() if key not in common}
        new_only = {key: value for key, value in new.items() if key not in common}
        for old_identity, old_plan in list(old_only.items()):
            matches = [
                (new_identity, new_plan)
                for new_identity, new_plan in new_only.items()
                if self._plan_signature(old_plan) == self._plan_signature(new_plan)
            ]
            if len(matches) != 1:
                continue
            new_identity, new_plan = matches[0]
            changes.append(
                NumericChange(
                    field="Plan name",
                    before=old_plan.name,
                    after=new_plan.name,
                    change_type="plan_renamed",
                )
            )
            old_only.pop(old_identity)
            new_only.pop(new_identity)

        for plan in sorted(old_only.values(), key=lambda item: item.identity):
            changes.extend(self._plan_presence_changes(plan, added=False))
        for plan in sorted(new_only.values(), key=lambda item: item.identity):
            changes.extend(self._plan_presence_changes(plan, added=True))

        old_global_free = bool(_GLOBAL_FREE_PLAN.search(previous.normalized_text))
        new_global_free = bool(_GLOBAL_FREE_PLAN.search(current.normalized_text))
        has_plan_free_change = any(
            item.change_type in {"free_tier_added", "free_tier_removed"} for item in changes
        )
        if old_global_free != new_global_free and not has_plan_free_change:
            changes.append(
                NumericChange(
                    field="Free tier",
                    before="available" if old_global_free else "not observed",
                    after="available" if new_global_free else "not observed",
                    change_type=("free_tier_added" if new_global_free else "free_tier_removed"),
                )
            )

        if (
            not old
            and not new
            and (
                _PRICE_OFFER.search(previous.normalized_text)
                or _PRICE_OFFER.search(current.normalized_text)
            )
        ):
            ignored.append(
                IgnoredChange(
                    reason="low_confidence_pricing",
                    detail="Unassociated published prices were ignored.",
                )
            )
        return changes, ignored

    def _parse_pricing_plans(
        self, sections: list[NormalizedSection]
    ) -> tuple[dict[str, PricingPlan], list[IgnoredChange], set[str]]:
        plans: dict[str, PricingPlan] = {}
        ambiguous: set[str] = set()
        ignored: list[IgnoredChange] = []
        for index, section in enumerate(sections):
            source = section.heading or f"section-{index}"
            labelled = self._labelled_plan_rows(section.content)
            for name, offer_text, confidence in labelled:
                self._merge_plan(
                    plans,
                    ambiguous,
                    ignored,
                    self._plan(name, offer_text, source, confidence),
                )
            if labelled or not section.heading or not self._reliable_plan_name(section.heading):
                continue
            if not self._contains_pricing_offer(section.content):
                continue
            self._merge_plan(
                plans,
                ambiguous,
                ignored,
                self._plan(section.heading, section.content, source, 1.0),
            )
        return plans, ignored, ambiguous

    def _labelled_plan_rows(self, content: str) -> list[tuple[str, str, float]]:
        rows: list[tuple[str, str, float]] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.count("|") >= 2:
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                is_separator = cells and all(
                    bool(re.fullmatch(r":?-{2,}:?", cell)) for cell in cells
                )
                if len(cells) >= 2 and not is_separator:
                    offer = " ".join(cells[1:])
                    if self._contains_pricing_offer(offer):
                        rows.append((cells[0], offer, 0.95))
                        continue
            labelled = _LABELLED_ROW.match(line)
            if labelled and self._contains_pricing_offer(labelled.group("offer")):
                rows.append((labelled.group("name"), labelled.group("offer"), 0.95))
                continue
            sentence = _PLAN_SENTENCE.match(line)
            if sentence and self._contains_pricing_offer(sentence.group("offer")):
                rows.append((sentence.group("name"), sentence.group("offer"), 0.9))
        return [item for item in rows if self._reliable_plan_name(item[0])]

    def _plan(self, name: str, value: str, source: str, confidence: float) -> PricingPlan:
        display = self._display_plan_name(name)
        offers, free, contact_sales = self._parse_offer(value)
        return PricingPlan(
            name=display,
            identity=self._plan_identity(display),
            source_section=source,
            confidence=confidence,
            offers=set(offers),
            free=free,
            contact_sales=contact_sales,
        )

    def _merge_plan(
        self,
        plans: dict[str, PricingPlan],
        ambiguous: set[str],
        ignored: list[IgnoredChange],
        plan: PricingPlan,
    ) -> None:
        if not plan.identity or plan.identity in ambiguous:
            return
        existing = plans.get(plan.identity)
        if existing is None:
            plans[plan.identity] = plan
            return
        old_by_key = {(item.currency, item.billing_period): item.amount for item in existing.offers}
        new_by_key = {(item.currency, item.billing_period): item.amount for item in plan.offers}
        conflicting_price = any(
            key in old_by_key and old_by_key[key] != amount for key, amount in new_by_key.items()
        )
        conflicting_state = (
            (existing.free and bool(plan.offers))
            or (plan.free and bool(existing.offers))
            or (existing.contact_sales and bool(plan.offers))
            or (plan.contact_sales and bool(existing.offers))
        )
        if conflicting_price or conflicting_state:
            plans.pop(plan.identity, None)
            ambiguous.add(plan.identity)
            ignored.append(
                IgnoredChange(
                    reason="ambiguous_pricing",
                    detail=f"Conflicting offers for {existing.name} were ignored.",
                )
            )
            return
        existing.offers.update(plan.offers)
        existing.free = existing.free or plan.free
        existing.contact_sales = existing.contact_sales or plan.contact_sales
        existing.confidence = min(existing.confidence, plan.confidence)

    def _compare_plan(self, old: PricingPlan, new: PricingPlan) -> list[NumericChange]:
        changes: list[NumericChange] = []
        if old.free != new.free:
            changes.append(
                NumericChange(
                    field=f"{new.name} free tier",
                    before="available" if old.free else "not observed",
                    after="available" if new.free else "not observed",
                    change_type="free_tier_added" if new.free else "free_tier_removed",
                )
            )

        old_by_key = {(item.currency, item.billing_period): item for item in old.offers}
        new_by_key = {(item.currency, item.billing_period): item for item in new.offers}
        for key in sorted(old_by_key.keys() & new_by_key.keys(), key=self._offer_key_sort):
            before = old_by_key[key]
            after = new_by_key[key]
            if before.amount == after.amount:
                continue
            changes.append(
                NumericChange(
                    field=self._offer_field(new, after),
                    before=before.amount,
                    after=after.amount,
                    currency=after.currency,
                    change_type=self._amount_change(before.amount, after.amount),
                )
            )

        old_unmatched = [old_by_key[key] for key in old_by_key.keys() - new_by_key.keys()]
        new_unmatched = [new_by_key[key] for key in new_by_key.keys() - old_by_key.keys()]
        periods = {item.billing_period for item in [*old_unmatched, *new_unmatched]}
        for period in sorted(periods, key=lambda item: item or ""):
            old_period = [item for item in old_unmatched if item.billing_period == period]
            new_period = [item for item in new_unmatched if item.billing_period == period]
            if len(old_period) == len(new_period) == 1:
                before = old_period[0]
                after = new_period[0]
                changes.append(
                    NumericChange(
                        field=self._offer_field(new, after),
                        before=before.amount,
                        after=after.amount,
                        currency=after.currency,
                        change_type="currency_changed",
                    )
                )
                old_unmatched.remove(before)
                new_unmatched.remove(after)

        changes.extend(
            NumericChange(
                field=self._offer_field(old, offer),
                before=offer.amount,
                currency=offer.currency,
                change_type="price_removed",
            )
            for offer in sorted(old_unmatched, key=self._offer_sort)
        )
        changes.extend(
            NumericChange(
                field=self._offer_field(new, offer),
                after=offer.amount,
                currency=offer.currency,
                change_type="price_added",
            )
            for offer in sorted(new_unmatched, key=self._offer_sort)
        )
        return changes

    def _plan_presence_changes(self, plan: PricingPlan, *, added: bool) -> list[NumericChange]:
        change_type = "plan_added" if added else "plan_removed"
        if plan.offers:
            return [
                NumericChange(
                    field=self._offer_field(plan, offer),
                    before=None if added else offer.amount,
                    after=offer.amount if added else None,
                    currency=offer.currency,
                    change_type=change_type,
                )
                for offer in sorted(plan.offers, key=self._offer_sort)
            ]
        state = "free" if plan.free else "contact sales" if plan.contact_sales else "published"
        return [
            NumericChange(
                field=f"{plan.name} plan",
                before=None if added else state,
                after=state if added else None,
                change_type=change_type,
            )
        ]

    @staticmethod
    def _plan_signature(plan: PricingPlan) -> tuple[tuple[PlanOffer, ...], bool, bool]:
        return (
            tuple(sorted(plan.offers, key=DeterministicChangeDetector._offer_sort)),
            plan.free,
            plan.contact_sales,
        )

    @staticmethod
    def _offer_field(plan: PricingPlan, offer: PlanOffer) -> str:
        period = offer.billing_period or "published"
        return f"{plan.name} {period} price"

    @staticmethod
    def _offer_key_sort(value: tuple[str, str | None]) -> tuple[str, str]:
        return value[0], value[1] or ""

    @staticmethod
    def _offer_sort(value: PlanOffer) -> tuple[str, str, Decimal]:
        return value.currency, value.billing_period or "", Decimal(value.amount)

    @staticmethod
    def _amount_change(before: str, after: str) -> str:
        try:
            old = Decimal(before)
            new = Decimal(after)
        except InvalidOperation:
            return "changed"
        return "increase" if new > old else "decrease" if new < old else "changed"

    @classmethod
    def _parse_offer(cls, value: str) -> tuple[list[PlanOffer], bool, bool]:
        offers: set[PlanOffer] = set()
        free = bool(
            re.fullmatch(
                r"\s*(?:free(?:\s+(?:plan|tier))?|no cost)[.!]?\s*",
                value,
                re.I,
            )
        )
        contact_sales = bool(_CONTACT_SALES.search(value))
        if _ZERO_PERIOD.search(value):
            free = True
        for match in _PRICE_OFFER.finditer(value):
            amount = cls._amount(match.group("amount") or match.group("amount_code") or "")
            if Decimal(amount) == 0:
                free = True
                continue
            currency = match.group("code") or _CURRENCIES.get(match.group("symbol") or "", "")
            period_value = match.group("period")
            offers.add(
                PlanOffer(
                    currency=currency.upper(),
                    billing_period=_PERIODS.get(period_value.casefold()) if period_value else None,
                    amount=amount,
                )
            )
        return sorted(offers, key=cls._offer_sort), free, contact_sales

    @staticmethod
    def _amount(value: str) -> str:
        number = Decimal(value.replace(",", "."))
        rendered = format(number, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered

    @staticmethod
    def _contains_pricing_offer(value: str) -> bool:
        return bool(
            _PRICE_OFFER.search(value)
            or _ZERO_PERIOD.search(value)
            or _CONTACT_SALES.search(value)
            or re.fullmatch(
                r"\s*(?:free(?:\s+(?:plan|tier))?|no cost)[.!]?\s*",
                value,
                re.I,
            )
        )

    @classmethod
    def _reliable_plan_name(cls, value: str) -> bool:
        display = cls._display_plan_name(value)
        identity = cls._plan_identity(display)
        return bool(
            identity
            and len(display) <= 80
            and len(identity.split("-")) <= 8
            and display.casefold() not in _GENERIC_PLAN_HEADINGS
            and not _PRICE_OFFER.search(display)
        )

    @staticmethod
    def _display_plan_name(value: str) -> str:
        normalized = re.sub(r"\s+", " ", value.strip(" |:-\t"))
        return re.sub(r"\s+(?:plan|tier)$", "", normalized, flags=re.I).strip() or normalized

    @staticmethod
    def _plan_identity(value: str) -> str:
        normalized = re.sub(r"\b(?:plan|tier)\b", " ", value.casefold())
        return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")

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
