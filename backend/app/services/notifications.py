from fastapi import status

from app.api.errors import AppError
from app.core.auth import AuthenticatedContext
from app.core.clock import Clock, SystemClock
from app.domain.background_jobs import (
    Notification,
    NotificationPage,
    NotificationSeverity,
    NotificationType,
    UnreadCountResponse,
)
from app.repositories.interfaces import Persistence


class NotificationService:
    def __init__(self, persistence: Persistence, clock: Clock | None = None) -> None:
        self._persistence = persistence
        self._clock = clock or SystemClock()

    async def list(
        self,
        context: AuthenticatedContext,
        *,
        unread_only: bool,
        severity: NotificationSeverity | None,
        notification_type: NotificationType | None,
        cursor: str | None,
        limit: int,
    ) -> NotificationPage:
        items = await self._persistence.notifications.list(
            context.organization_id,
            unread_only=unread_only,
            severity=severity,
            notification_type=notification_type,
            cursor=cursor,
            limit=limit + 1,
        )
        return NotificationPage(
            items=items[:limit],
            next_cursor=items[limit - 1].id if len(items) > limit else None,
        )

    async def unread_count(self, context: AuthenticatedContext) -> UnreadCountResponse:
        return UnreadCountResponse(
            unread_count=await self._persistence.notifications.unread_count(context.organization_id)
        )

    async def mark_read(self, context: AuthenticatedContext, notification_id: str) -> Notification:
        notification = await self._persistence.notifications.mark_read(
            context.organization_id, notification_id, self._clock.now()
        )
        if notification is None:
            raise AppError(
                code="NOTIFICATION_NOT_FOUND",
                message="The notification was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return notification

    async def mark_all_read(self, context: AuthenticatedContext) -> UnreadCountResponse:
        await self._persistence.notifications.mark_all_read(
            context.organization_id, self._clock.now()
        )
        return UnreadCountResponse(unread_count=0)
