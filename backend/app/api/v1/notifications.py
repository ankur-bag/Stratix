from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import Authenticated, NotificationsService
from app.domain.background_jobs import (
    Notification,
    NotificationPage,
    NotificationSeverity,
    NotificationType,
    UnreadCountResponse,
)

router = APIRouter()


@router.get("/notifications", response_model=NotificationPage)
async def list_notifications(
    context: Authenticated,
    service: NotificationsService,
    unread_only: bool = False,
    severity: NotificationSeverity | None = None,
    notification_type: NotificationType | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> NotificationPage:
    return await service.list(
        context,
        unread_only=unread_only,
        severity=severity,
        notification_type=notification_type,
        cursor=cursor,
        limit=limit,
    )


@router.get("/notifications/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    context: Authenticated, service: NotificationsService
) -> UnreadCountResponse:
    return await service.unread_count(context)


@router.post("/notifications/{notification_id}/read", response_model=Notification)
async def mark_notification_read(
    notification_id: str,
    context: Authenticated,
    service: NotificationsService,
) -> Notification:
    return await service.mark_read(context, notification_id)


@router.post("/notifications/read-all", response_model=UnreadCountResponse)
async def mark_all_notifications_read(
    context: Authenticated, service: NotificationsService
) -> UnreadCountResponse:
    return await service.mark_all_read(context)
