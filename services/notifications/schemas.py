"""Notifications API contracts (API_DESIGN.md)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from libs.db.models import Notification, NotificationType


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: NotificationType
    transaction_id: str | None = None
    message: str
    created_at: datetime
    read: bool

    @classmethod
    def from_model(cls, notification: Notification) -> NotificationRead:
        return cls.model_validate(notification)


class NotificationList(BaseModel):
    notifications: list[NotificationRead]
    unread_count: int


class NotificationPatch(BaseModel):
    read: bool
