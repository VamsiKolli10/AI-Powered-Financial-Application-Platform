"""Read API for alerts. Notifications are created by the consumer, never by a client."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.auth import current_user_id
from libs.common.errors import NotFoundError
from libs.db.repositories import NotificationRepository
from libs.db.session import get_session
from services.notifications.schemas import (
    NotificationList,
    NotificationPatch,
    NotificationRead,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationList, summary="Recent alerts for the current user")
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> NotificationList:
    rows = await NotificationRepository(session).list_for_user(user_id, limit=limit)
    return NotificationList(
        notifications=[NotificationRead.from_model(n) for n in rows],
        unread_count=sum(1 for n in rows if not n.read),
    )


@router.patch("/{notification_id}", response_model=NotificationRead, summary="Mark as read")
async def patch_notification(
    notification_id: str,
    payload: NotificationPatch,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> NotificationRead:
    repo = NotificationRepository(session)
    notification = await repo.get(notification_id)
    # Same 404 for missing and not-yours: never confirm another user's alert exists.
    if notification is None or notification.user_id != user_id:
        raise NotFoundError(f"Notification '{notification_id}' was not found.")
    notification.read = payload.read
    return NotificationRead.from_model(notification)
