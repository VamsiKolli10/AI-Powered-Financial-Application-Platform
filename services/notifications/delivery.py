"""Alert delivery.

A portfolio project has no real email or push provider, so delivery is logged. The
seam is what matters: swapping in SES or APNs means implementing this Protocol, not
touching the consumer or the rules.
"""

from __future__ import annotations

from typing import Protocol

from libs.common.logging import get_logger
from services.notifications.rules import Alert

log = get_logger("notifications.delivery")


class AlertChannel(Protocol):
    async def send(self, alert: Alert) -> None: ...


class LoggingChannel:
    """Delivery for local development: alerts show up in the service logs."""

    async def send(self, alert: Alert) -> None:
        log.info(
            "alert_delivered",
            channel="log",
            user_id=alert.user_id,
            type=alert.type.value,
            message=alert.message,
            transaction_id=alert.transaction_id,
        )


class CollectingChannel:
    """Test double: keeps what it was asked to send."""

    def __init__(self) -> None:
        self.sent: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        self.sent.append(alert)
