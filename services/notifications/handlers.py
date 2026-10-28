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
from services.notifications.rules import Alert, AlertThresholds, evaluate

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
    # Same currency as the transaction: a budget is crossed in one currency, not a blend.
    totals = await TransactionRepository(session).spend_by_category(
        account_ids=account_ids, date_from=start, date_to=end, currency=event.currency
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
            repo = NotificationRepository(session)
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

            # Include earlier pending deliveries even if ledger corrections mean
            # the rule no longer fires when this event is replayed.
            transaction_id = getattr(event, "transaction_id", None)
            user_id = getattr(event, "user_id", None)
            pending = (
                await repo.pending_for_transaction(user_id=user_id, transaction_id=transaction_id)
                if transaction_id is not None and user_id is not None
                else []
            )
            deliveries = [
                (
                    row.id,
                    Alert(
                        user_id=row.user_id,
                        type=row.type,
                        message=row.message,
                        transaction_id=row.transaction_id,
                        dedupe_key=row.dedupe_key,
                    ),
                )
                for row in pending
            ]

        # Deliver only after the rows are committed, so a delivery failure cannot
        # produce an alert the user was told about but that we never stored.
        for notification_id, alert in deliveries:
            await self.channel.send(alert)
            async with self.session_scope() as session:
                notification = await NotificationRepository(session).get(notification_id)
                if notification is not None:
                    notification.delivered_at = datetime.now(UTC)
