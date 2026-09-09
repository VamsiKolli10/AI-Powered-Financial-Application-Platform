"""Alert rules, tested as pure functions."""

from datetime import UTC, datetime
from decimal import Decimal

from libs.db.models import NotificationType
from libs.events.schemas import TransactionAnomalyFlagged, TransactionCategorized
from services.notifications.rules import AlertThresholds, evaluate

THRESHOLDS = AlertThresholds(large_transaction=Decimal("500"), budget_monthly_limit=Decimal("1500"))


def _categorized(amount, **overrides):
    base = {
        "transaction_id": "tx_1",
        "account_id": "acct_1",
        "user_id": "usr_1",
        "category": "shopping",
        "amount": Decimal(str(amount)),
        "merchant": "Best Buy",
        "anomaly_score": 0.1,
        "occurred_at": datetime(2026, 9, 5, 14, 30, tzinfo=UTC),
    }
    return TransactionCategorized(**{**base, **overrides})


def test_ordinary_transaction_raises_nothing():
    assert evaluate(_categorized(-42.50), thresholds=THRESHOLDS) == []


def test_large_outflow_raises_an_alert():
    alerts = evaluate(_categorized(-1349.99), thresholds=THRESHOLDS)
    assert len(alerts) == 1
    assert alerts[0].type is NotificationType.LARGE_TRANSACTION
    assert "$1,349.99" in alerts[0].message


def test_large_inflow_raises_nothing():
    # Nobody needs warning about a big paycheck.
    assert evaluate(_categorized(4200.00, category="income"), thresholds=THRESHOLDS) == []


def test_amount_exactly_on_the_threshold_alerts():
    alerts = evaluate(_categorized(-500), thresholds=THRESHOLDS)
    assert len(alerts) == 1


def test_anomaly_event_raises_an_anomaly_alert():
    event = TransactionAnomalyFlagged(
        transaction_id="tx_9",
        account_id="acct_1",
        user_id="usr_1",
        amount=Decimal("-4820"),
        merchant="Luxury Watch",
        anomaly_score=0.94,
        reason="amount_outlier",
    )
    alerts = evaluate(event, thresholds=THRESHOLDS)
    assert alerts[0].type is NotificationType.ANOMALY_FLAGGED
    assert "Luxury Watch" in alerts[0].message


def test_budget_threshold_alert_when_month_to_date_spend_is_high():
    alerts = evaluate(
        _categorized(-60), thresholds=THRESHOLDS, category_spend_this_month=Decimal("1600")
    )
    assert [a.type for a in alerts] == [NotificationType.BUDGET_THRESHOLD]
    assert "Shopping" in alerts[0].message


def test_budget_alert_dedupes_per_category_per_month_not_per_transaction():
    a = evaluate(
        _categorized(-60, transaction_id="tx_a"),
        thresholds=THRESHOLDS,
        category_spend_this_month=Decimal("1600"),
    )[0]
    b = evaluate(
        _categorized(-70, transaction_id="tx_b"),
        thresholds=THRESHOLDS,
        category_spend_this_month=Decimal("1700"),
    )[0]
    assert a.dedupe_key == b.dedupe_key


def test_one_transaction_can_raise_two_alerts():
    alerts = evaluate(
        _categorized(-900), thresholds=THRESHOLDS, category_spend_this_month=Decimal("2000")
    )
    assert {a.type for a in alerts} == {
        NotificationType.LARGE_TRANSACTION,
        NotificationType.BUDGET_THRESHOLD,
    }


def test_dedupe_keys_are_unique_per_transaction_and_kind():
    alerts = evaluate(
        _categorized(-900), thresholds=THRESHOLDS, category_spend_this_month=Decimal("2000")
    )
    assert len({a.dedupe_key for a in alerts}) == 2
