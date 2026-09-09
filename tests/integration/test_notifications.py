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
