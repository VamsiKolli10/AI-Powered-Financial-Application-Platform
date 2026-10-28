"""Notifications: event handling, idempotency, and the read API."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from libs.db.models import NotificationType
from libs.events.schemas import TransactionAnomalyFlagged, TransactionCategorized
from services.notifications.delivery import CollectingChannel
from services.notifications.handlers import NotificationHandler
from services.notifications.rules import AlertThresholds
from tests.conftest import OTHER_USER_ID, TEST_USER_ID


@pytest.fixture
def channel():
    return CollectingChannel()


@pytest.fixture
def handler(session_scope_factory, channel):
    return NotificationHandler(
        session_scope=session_scope_factory,
        channel=channel,
        thresholds=AlertThresholds(
            large_transaction=Decimal("500"), budget_monthly_limit=Decimal("1500")
        ),
    )


def _large_purchase(transaction_id="tx_big"):
    return TransactionCategorized(
        transaction_id=transaction_id,
        account_id="acct_test",
        user_id=TEST_USER_ID,
        category="electronics",
        amount=Decimal("-1349.99"),
        merchant="Best Buy",
        anomaly_score=0.3,
        occurred_at=datetime(2026, 9, 5, 14, 30, tzinfo=UTC),
    )


async def test_large_transaction_event_creates_and_delivers_an_alert(handler, channel, session):
    await handler(_large_purchase())

    from libs.db.repositories import NotificationRepository

    stored = await NotificationRepository(session).list_for_user(TEST_USER_ID)
    assert len(stored) == 1
    assert stored[0].type is NotificationType.LARGE_TRANSACTION
    assert len(channel.sent) == 1


async def test_redelivered_event_does_not_duplicate_the_alert(handler, channel, session):
    """Kafka is at-least-once, so the handler must be idempotent."""
    event = _large_purchase()
    await handler(event)
    await handler(event)

    from libs.db.repositories import NotificationRepository

    stored = await NotificationRepository(session).list_for_user(TEST_USER_ID)
    assert len(stored) == 1
    assert len(channel.sent) == 1  # and the user is not told twice


async def test_failed_delivery_remains_pending_and_retries_after_handler_restart(
    session_scope_factory, session
):
    from unittest.mock import AsyncMock

    from libs.db.repositories import NotificationRepository

    failing = AsyncMock()
    failing.send.side_effect = RuntimeError("channel unavailable")
    first = NotificationHandler(session_scope=session_scope_factory, channel=failing)
    event = _large_purchase()
    with pytest.raises(RuntimeError, match="channel unavailable"):
        await first(event)
    pending = await NotificationRepository(session).pending_for_transaction(
        user_id=TEST_USER_ID, transaction_id=event.transaction_id
    )
    assert len(pending) == 1

    recovered = CollectingChannel()
    restarted = NotificationHandler(session_scope=session_scope_factory, channel=recovered)
    await restarted(event)
    await restarted(event)
    assert len(recovered.sent) == 1
    await session.refresh(pending[0])
    assert pending[0].delivered_at is not None


async def test_partial_delivery_retry_sends_only_remaining_alerts(
    session_scope_factory, session, monkeypatch
):
    from unittest.mock import AsyncMock

    # Raise both a large-purchase and a budget alert for the same event.
    monkeypatch.setattr(
        "services.notifications.handlers._category_spend_this_month",
        AsyncMock(return_value=Decimal("1600")),
    )
    channel = AsyncMock()
    channel.send.side_effect = [None, RuntimeError("second send failed"), None]
    handler = NotificationHandler(session_scope=session_scope_factory, channel=channel)
    event = _large_purchase()
    with pytest.raises(RuntimeError, match="second send failed"):
        await handler(event)
    # Even a ledger correction removing the budget trigger must not lose pending work.
    monkeypatch.setattr(
        "services.notifications.handlers._category_spend_this_month",
        AsyncMock(return_value=Decimal("0")),
    )
    await handler(event)
    sent = [call.args[0].type for call in channel.send.await_args_list]
    assert sent == [
        NotificationType.LARGE_TRANSACTION,
        NotificationType.BUDGET_THRESHOLD,
        NotificationType.BUDGET_THRESHOLD,
    ]


async def test_budget_alerts_for_other_users_and_currencies_are_stored(
    handler, channel, session, monkeypatch
):
    from unittest.mock import AsyncMock

    from libs.db.repositories import NotificationRepository

    monkeypatch.setattr(
        "services.notifications.handlers._category_spend_this_month",
        AsyncMock(return_value=Decimal("1600")),
    )
    for user, account, currency in [
        (TEST_USER_ID, "acct_test", "USD"),
        (OTHER_USER_ID, "acct_other", "USD"),
        (TEST_USER_ID, "acct_test", "EUR"),
    ]:
        event = _large_purchase().model_copy(
            update={
                "user_id": user,
                "account_id": account,
                "currency": currency,
                "amount": Decimal("-60"),
                "transaction_id": f"tx_{user}_{currency}",
            }
        )
        await handler(event)
        await handler(event)
    assert len(channel.sent) == 3
    assert len(await NotificationRepository(session).list_for_user(TEST_USER_ID)) == 2
    assert len(await NotificationRepository(session).list_for_user(OTHER_USER_ID)) == 1


async def test_ordinary_transaction_creates_nothing(handler, channel, session):
    await handler(
        TransactionCategorized(
            transaction_id="tx_small",
            account_id="acct_test",
            user_id=TEST_USER_ID,
            category="dining_coffee",
            amount=Decimal("-6.75"),
            merchant="Starbucks",
            anomaly_score=0.01,
            occurred_at=datetime(2026, 9, 5, 14, 30, tzinfo=UTC),
        )
    )
    assert channel.sent == []


async def test_anomaly_event_creates_an_anomaly_alert(handler, channel):
    await handler(
        TransactionAnomalyFlagged(
            transaction_id="tx_odd",
            account_id="acct_test",
            user_id=TEST_USER_ID,
            amount=Decimal("-4820"),
            merchant="Luxury Watch",
            anomaly_score=0.94,
            reason="amount_outlier",
        )
    )
    assert channel.sent[0].type is NotificationType.ANOMALY_FLAGGED


async def test_api_lists_alerts_with_unread_count(handler, make_client):
    from services.notifications.main import app

    await handler(_large_purchase())
    client = await make_client(app)

    response = await client.get("/notifications")

    assert response.status_code == 200
    body = response.json()
    assert body["unread_count"] == 1
    assert body["notifications"][0]["type"] == "large_transaction"


async def test_api_marks_an_alert_read(handler, make_client):
    from services.notifications.main import app

    await handler(_large_purchase())
    client = await make_client(app)
    listed = (await client.get("/notifications")).json()["notifications"][0]

    response = await client.patch(f"/notifications/{listed['id']}", json={"read": True})

    assert response.status_code == 200
    assert response.json()["read"] is True
    assert (await client.get("/notifications")).json()["unread_count"] == 0


async def test_api_hides_another_users_alerts(handler, make_client):
    from services.notifications.main import app

    await handler(_large_purchase())
    client = await make_client(app)
    mine = (await client.get("/notifications")).json()["notifications"][0]

    response = await client.patch(
        f"/notifications/{mine['id']}",
        json={"read": True},
        headers={"X-User-Id": OTHER_USER_ID},
    )

    assert response.status_code == 404


async def test_budget_threshold_counts_only_the_transactions_currency(handler, channel, session):
    from libs.db.models import CategorySource, Transaction, TransactionStatus
    from tests.conftest import within_current_month

    when = within_current_month(0.5)
    # 1,400 USD of groceries already this month, plus 900 EUR that must not count toward it.
    for external_id, amount, currency in (
        ("g_usd", "-1400.00", "USD"),
        ("g_eur", "-900.00", "EUR"),
    ):
        session.add(
            Transaction(
                account_id="acct_test",
                external_tx_id=external_id,
                amount=Decimal(amount),
                currency=currency,
                description="GROCER",
                normalized_description="grocer",
                status=TransactionStatus.CATEGORIZED,
                category_slug="groceries",
                category_source=CategorySource.RULES,
                occurred_at=when,
            )
        )
    await session.commit()

    event = TransactionCategorized(
        transaction_id="tx_g",
        account_id="acct_test",
        user_id=TEST_USER_ID,
        category="groceries",
        amount=Decimal("-50.00"),
        currency="USD",
        merchant="Grocer",
        anomaly_score=0.1,
        occurred_at=when,
    )
    await handler(event)

    # 1,400 USD < 1,500: no budget alert, even though USD + EUR would exceed it.
    assert not [a for a in channel.sent if a.type is NotificationType.BUDGET_THRESHOLD]
