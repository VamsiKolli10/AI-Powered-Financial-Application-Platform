"""Phase 4's done-criteria: one POST visibly reaches three services.

The broker is swapped for an in-process publisher that hands events straight to the
Notifications handler. That exercises everything the services own - event construction,
publish-after-commit ordering, the alert rules, the handler's idempotency and the
Insights aggregation - without needing a running Kafka. The Kafka-specific pieces
(producer config, consumer commit rules) are covered in tests/unit/test_events.py.
"""

from decimal import Decimal

import pytest

from libs.events import InMemoryEventPublisher
from services.notifications.delivery import CollectingChannel
from services.notifications.handlers import NotificationHandler
from services.notifications.rules import AlertThresholds


@pytest.fixture
def channel():
    return CollectingChannel()


@pytest.fixture
def publisher(session_scope_factory, channel):
    """Transactions publishes; Notifications consumes, in-process."""
    handler = NotificationHandler(
        session_scope=session_scope_factory,
        channel=channel,
        thresholds=AlertThresholds(
            large_transaction=Decimal("500"), budget_monthly_limit=Decimal("1500")
        ),
    )
    return InMemoryEventPublisher(handlers=[handler])


@pytest.fixture
async def transactions_client(app, make_client, publisher):
    app.state.publisher = publisher
    client = await make_client(app)
    yield client
    app.state.publisher = None


def _payload(**overrides):
    base = {
        "account_id": "acct_test",
        "external_tx_id": "fanout_1",
        "amount": -6.75,
        "currency": "USD",
        "description": "STARBUCKS #4412 SEATTLE WA",
        "occurred_at": "2026-09-05T14:30:00Z",
    }
    return {**base, **overrides}


async def test_every_categorized_transaction_publishes_an_event(transactions_client, publisher):
    await transactions_client.post("/transactions", json=_payload())

    events = publisher.of_type("transaction.categorized")
    assert len(events) == 1
    assert events[0].category == "dining_coffee"
    assert events[0].user_id  # identity travels with the event


async def test_ordinary_transaction_raises_no_alert(transactions_client, channel):
    await transactions_client.post("/transactions", json=_payload())
    assert channel.sent == []


async def test_large_transaction_reaches_notifications(transactions_client, make_client, channel):
    from services.notifications.main import app as notifications_app

    await transactions_client.post(
        "/transactions",
        json=_payload(external_tx_id="fanout_big", amount=-1349.99, description="BEST BUY #221"),
    )

    # Delivered...
    assert [a.type.value for a in channel.sent] == ["large_transaction"]

    # ...and readable through the Notifications API.
    notifications = await make_client(notifications_app)
    body = (await notifications.get("/notifications")).json()
    assert body["unread_count"] == 1
    assert "1,349.99" in body["notifications"][0]["message"]


async def test_outlier_publishes_an_anomaly_event_and_alert(
    transactions_client, publisher, channel
):
    # Seed a normal spending pattern so the outlier is genuinely out of character.
    for i in range(6):
        await transactions_client.post(
            "/transactions",
            json=_payload(external_tx_id=f"normal_{i}", amount=-12.50),
        )

    await transactions_client.post(
        "/transactions",
        json=_payload(
            external_tx_id="outlier", amount=-4820.00, description="LUXURY WATCH BOUTIQUE"
        ),
    )

    anomalies = publisher.of_type("transaction.anomaly_flagged")
    assert len(anomalies) == 1
    assert anomalies[0].anomaly_score >= 0.8
    assert anomalies[0].reason == "amount_outlier"
    assert "anomaly_flagged" in {a.type.value for a in channel.sent}


async def test_the_same_transaction_reaches_the_insights_summary(transactions_client, make_client):
    from services.insights.main import app as insights_app

    insights_app.state.summarizer = None
    await transactions_client.post(
        "/transactions", json=_payload(external_tx_id="fanout_ins", amount=-80.00)
    )

    insights = await make_client(insights_app)
    body = (await insights.get("/insights/summary")).json()

    assert Decimal(body["total_spend"]) == Decimal("80.00")
    assert body["by_category"][0]["category"] == "Dining & Coffee"


async def test_replayed_ingest_publishes_nothing_new(transactions_client, publisher):
    await transactions_client.post("/transactions", json=_payload())
    await transactions_client.post("/transactions", json=_payload())

    # Idempotent ingest means no second event, so downstream sees no duplicate.
    assert len(publisher.of_type("transaction.categorized")) == 1
