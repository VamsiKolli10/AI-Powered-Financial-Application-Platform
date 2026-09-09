"""Turns transaction events into stored, delivered alerts."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.logging import get_logger
from libs.db.repositories import AccountRepository, NotificationRepository, TransactionRepository
from libs.events.schemas import BaseEvent, TransactionCategorized
from services.notifications.delivery import AlertChannel
from services.notifications.rules import AlertThresholds, evaluate

log = get_logger("notifications")

SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _month_bounds(when: datetime) -> tuple[datetime, datetime]:
    start = when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    end = when if when.tzinfo else when.replace(tzinfo=UTC)
    return start, end


async def _category_spend_this_month(
    session: AsyncSession, event: TransactionCategorized
) -> Decimal | None:
    """Month-to-date spend in this transaction's category, for the budget rule."""
    if not event.category:
        return None
    account = await AccountRepository(session).get(event.account_id)
    if account is None:
        return None
    account_ids = await AccountRepository(session).ids_for_user(account.user_id)
    start, end = _month_bounds(event.occurred_at)
    totals = await TransactionRepository(session).spend_by_category(
        account_ids=account_ids, date_from=start, date_to=end
    )
    for slug, total, _count in totals:
        if slug == event.category:
            return total
    return None


class NotificationHandler:
    """Event handler passed to the Kafka consumer.

    Idempotent by construction: alerts carry a dedupe key, so a redelivered event
    produces no duplicate notification.
    """

    def __init__(
        self,
        *,
        session_scope: SessionScope,
        channel: AlertChannel,
        thresholds: AlertThresholds | None = None,
    ) -> None:
        self.session_scope = session_scope
        self.channel = channel
        self.thresholds = thresholds or AlertThresholds()

    async def __call__(self, event: BaseEvent) -> None:
        async with self.session_scope() as session:
            spend = (
                await _category_spend_this_month(session, event)
                if isinstance(event, TransactionCategorized)
                else None
            )
            alerts = evaluate(event, thresholds=self.thresholds, category_spend_this_month=spend)
            if not alerts:
                return

            repo = NotificationRepository(session)
            created = []
            for alert in alerts:
                notification = await repo.create_if_new(
                    user_id=alert.user_id,
                    type_=alert.type,
                    message=alert.message,
                    dedupe_key=alert.dedupe_key,
                    transaction_id=alert.transaction_id,
                )
                if notification is None:
                    log.info("alert_suppressed_duplicate", dedupe_key=alert.dedupe_key)
                    continue
                created.append(alert)

        # Deliver only after the rows are committed, so a delivery failure cannot
        # produce an alert the user was told about but that we never stored.
        for alert in created:
            await self.channel.send(alert)
