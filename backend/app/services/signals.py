from datetime import datetime

from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.domain.onboarding import new_id, utc_now
from app.domain.scanning import ChangeSet, CompetitorSnapshot
from app.domain.signals import (
    CompetitorSignal,
    SignalPage,
    SignalStatus,
    SignalStatusUpdate,
    SignalType,
)
from app.repositories.interfaces import Persistence


class DeterministicSignalBuilder:
    """Turn one verified change set into one objective, evidence-linked signal."""

    def build(
        self,
        *,
        previous: CompetitorSnapshot,
        current: CompetitorSnapshot,
        change_set: ChangeSet,
    ) -> CompetitorSignal | None:
        if not change_set.changed:
            return None
        signal_type = self._type(current.page_type.value, change_set)
        before = self._before(change_set)
        after = self._after(change_set) or current.normalized_text[:500]
        facts = self._facts(change_set)
        return CompetitorSignal(
            id=new_id(),
            organization_id=current.organization_id,
            competitor_id=current.competitor_id,
            source_id=current.source_id,
            scan_id=current.scan_id,
            type=signal_type,
            title=self._title(signal_type),
            summary=self._summary(signal_type, change_set),
            observed_facts=facts,
            before_excerpt=before[:500] if before else None,
            after_excerpt=after[:500],
            changed_sections=[
                item.heading or item.identifier
                for item in [*change_set.added_sections, *change_set.removed_sections]
            ]
            + [item.heading or item.identifier for item in change_set.modified_sections],
            numeric_changes=change_set.numeric_changes,
            importance_score=change_set.significance_score,
            confidence=0.95 if change_set.numeric_changes else 0.85,
            source_url=current.canonical_url,
            previous_snapshot_id=previous.id,
            current_snapshot_id=current.id,
            observed_at=current.fetched_at,
            created_at=utc_now(),
        )

    @staticmethod
    def _type(page_type: str, change_set: ChangeSet) -> SignalType:
        if page_type == "pricing" and change_set.numeric_changes:
            return SignalType.PRICING_CHANGE
        if page_type == "features" and change_set.added_sections:
            return SignalType.FEATURE_LAUNCH
        if page_type == "features" and change_set.removed_sections:
            return SignalType.FEATURE_REMOVAL
        if page_type == "changelog" and change_set.added_sections:
            return SignalType.CHANGELOG_UPDATE
        if page_type == "documentation":
            return SignalType.DOCUMENTATION_CHANGE
        if page_type == "integrations":
            return SignalType.INTEGRATION_CHANGE
        if page_type == "customers":
            return SignalType.CUSTOMER_PROOF_CHANGE
        if page_type in {"homepage", "about"} and change_set.modified_sections:
            return SignalType.POSITIONING_CHANGE
        if page_type in {"product", "solutions"}:
            return SignalType.PRODUCT_CHANGE
        return SignalType.CONTENT_CHANGE

    @staticmethod
    def _title(signal_type: SignalType) -> str:
        return {
            SignalType.PRICING_CHANGE: "Published pricing changed",
            SignalType.FEATURE_LAUNCH: "Feature content was added",
            SignalType.FEATURE_REMOVAL: "Feature content was removed",
            SignalType.CHANGELOG_UPDATE: "A changelog entry was added",
            SignalType.POSITIONING_CHANGE: "Public positioning changed",
            SignalType.DOCUMENTATION_CHANGE: "Documentation changed",
            SignalType.INTEGRATION_CHANGE: "Integration content changed",
            SignalType.CUSTOMER_PROOF_CHANGE: "Customer evidence changed",
            SignalType.PRODUCT_CHANGE: "Product content changed",
        }.get(signal_type, "Public page content changed")

    @staticmethod
    def _summary(signal_type: SignalType, change_set: ChangeSet) -> str:
        return (
            f"Observed {signal_type.value.replace('_', ' ')} with "
            f"{len(change_set.added_sections)} added, {len(change_set.removed_sections)} removed, "
            f"and {len(change_set.modified_sections)} modified sections."
        )

    @staticmethod
    def _facts(change_set: ChangeSet) -> list[str]:
        facts = [
            *(
                f"Section added: {item.heading or item.identifier}."
                for item in change_set.added_sections
            ),
            *(
                f"Section removed: {item.heading or item.identifier}."
                for item in change_set.removed_sections
            ),
            *(
                f"Section modified: {item.heading or item.identifier}."
                for item in change_set.modified_sections
            ),
            *(
                f"{item.field} changed from {item.before or 'not observed'} to "
                f"{item.after or 'not observed'}."
                for item in change_set.numeric_changes
            ),
        ]
        return facts or ["The normalized public page content changed."]

    @staticmethod
    def _before(change_set: ChangeSet) -> str:
        if change_set.modified_sections:
            return change_set.modified_sections[0].before
        if change_set.removed_sections:
            return change_set.removed_sections[0].content
        return ""

    @staticmethod
    def _after(change_set: ChangeSet) -> str:
        if change_set.modified_sections:
            return change_set.modified_sections[0].after
        if change_set.added_sections:
            return change_set.added_sections[0].content
        return ""


class CompetitorSignalService:
    """Expose immutable signal evidence and mutable review state."""

    def __init__(self, persistence: Persistence) -> None:
        self._persistence = persistence

    async def list(
        self,
        context: AuthenticatedContext,
        competitor_id: str,
        *,
        signal_type: SignalType | None,
        signal_status: SignalStatus | None,
        minimum_importance: float,
        source_id: str | None,
        observed_after: datetime | None,
        observed_before: datetime | None,
        scan_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> SignalPage:
        competitor = await self._persistence.competitors.get_by_id(
            context.organization_id, competitor_id
        )
        if competitor is None:
            raise AppError(
                code="COMPETITOR_NOT_FOUND",
                message="The competitor was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        items = await self._persistence.competitor_signals.list_for_competitor(
            context.organization_id,
            competitor_id,
            signal_type=signal_type,
            signal_status=signal_status,
            minimum_importance=minimum_importance,
            source_id=source_id,
            observed_after=observed_after,
            observed_before=observed_before,
            scan_id=scan_id,
            cursor=cursor,
            limit=limit + 1,
        )
        return SignalPage(
            items=items[:limit], next_cursor=items[limit - 1].id if len(items) > limit else None
        )

    async def get(self, context: AuthenticatedContext, signal_id: str) -> CompetitorSignal:
        signal = await self._persistence.competitor_signals.get_by_id(
            context.organization_id, signal_id
        )
        if signal is None:
            raise AppError(
                code="SIGNAL_NOT_FOUND",
                message="The competitor signal was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return signal

    async def update_status(
        self,
        context: AuthenticatedContext,
        signal_id: str,
        request: SignalStatusUpdate,
    ) -> CompetitorSignal:
        signal = await self._persistence.competitor_signals.update_status(
            context.organization_id, signal_id, request.status
        )
        if signal is None:
            raise AppError(
                code="SIGNAL_NOT_FOUND",
                message="The competitor signal was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return signal
