"""Alert rules: which events become a notification, and what they say.

Pure functions over an event plus a little context, so every rule is unit-testable
without Kafka, a database, or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from libs.db.categories import CATEGORY_NAMES
from libs.db.models import NotificationType
from libs.events.schemas import (
    BaseEvent,
    TransactionAnomalyFlagged,
    TransactionCategorized,
)


@dataclass(frozen=True)
class Alert:
    user_id: str
    type: NotificationType
    message: str
    transaction_id: str | None = None
    # Deduplication key: one alert of a given kind per transaction, however many
    # times the event is redelivered.
    dedupe_key: str = ""


def _money(amount: Decimal, currency: str = "USD") -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, f"{currency} ")
    return f"{symbol}{abs(amount):,.2f}"


@dataclass(frozen=True)
class AlertThresholds:
    large_transaction: Decimal = Decimal("500")
    budget_monthly_limit: Decimal = Decimal("1500")


def evaluate(
    event: BaseEvent,
    *,
    thresholds: AlertThresholds,
    category_spend_this_month: Decimal | None = None,
) -> list[Alert]:
    """All alerts raised by one event."""
    if isinstance(event, TransactionAnomalyFlagged):
        return [
            Alert(
                user_id=event.user_id,
                type=NotificationType.ANOMALY_FLAGGED,
                message=(
                    f"Unusual transaction: {_money(event.amount, event.currency)} at "
                    f"{event.merchant or 'an unfamiliar merchant'}."
                ),
                transaction_id=event.transaction_id,
                dedupe_key=f"anomaly:{event.transaction_id}",
            )
        ]

    if not isinstance(event, TransactionCategorized):
        return []

    alerts: list[Alert] = []

    # Outflows only: a large deposit is not something to warn anyone about.
    if event.amount < 0 and abs(event.amount) >= thresholds.large_transaction:
        alerts.append(
            Alert(
                user_id=event.user_id,
                type=NotificationType.LARGE_TRANSACTION,
                message=(
                    f"Large transaction: {_money(event.amount, event.currency)} at "
                    f"{event.merchant or 'an unknown merchant'}."
                ),
                transaction_id=event.transaction_id,
                dedupe_key=f"large:{event.transaction_id}",
            )
        )

    if (
        category_spend_this_month is not None
        and event.category
        and category_spend_this_month >= thresholds.budget_monthly_limit
    ):
        category = CATEGORY_NAMES.get(event.category, event.category)
        alerts.append(
            Alert(
                user_id=event.user_id,
                type=NotificationType.BUDGET_THRESHOLD,
                message=(
                    f"{category} spending has reached "
                    f"{_money(category_spend_this_month, event.currency)} this month."
                ),
                transaction_id=event.transaction_id,
                # One budget alert per category per month, not per transaction.
                dedupe_key=f"budget:{event.category}:{event.occurred_at:%Y-%m}",
            )
        )

    return alerts
