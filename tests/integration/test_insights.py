"""Insights: aggregates are exact, the AI sentence sits on top of them."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from libs.db.models import CategorySource, Transaction, TransactionStatus
from libs.llm_client.cache import ClassificationCache, InMemoryCache
from libs.llm_client.client import LLMClient
from libs.llm_client.rate_limit import InMemoryRateLimiter
from libs.llm_client.summarize import SpendingSummarizer
from tests.conftest import within_current_month

NOW = datetime.now(UTC)


class ScriptedCompleter:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def complete(self, *, messages, model, timeout, max_tokens):
        self.calls += 1
        self.last_payload = messages[-1]["content"]
        if isinstance(self.reply, Exception):
            raise self.reply
        if self.reply == "__draft__":
            return json.dumps({"summary": messages[-1]["content"].split("DRAFT: ", 1)[1]})
        return self.reply


def _summarizer(reply):
    return SpendingSummarizer(
        LLMClient(ScriptedCompleter(reply), max_attempts=1),
        cache=ClassificationCache(InMemoryCache()),
        rate_limiter=InMemoryRateLimiter(per_minute=600),
    )


@pytest.fixture
async def spending(session):
    """Two categories this month, plus history in the previous month."""
    rows = [
        ("dining_coffee", "-40.00", 2),
        ("dining_coffee", "-60.00", 3),
        ("groceries", "-150.00", 4),
        ("income", "4200.00", 1),  # inflow: must not count as spend
    ]
    for i, (category, amount, days_ago) in enumerate(rows):
        session.add(
            Transaction(
                account_id="acct_test",
                external_tx_id=f"ins_{i}",
                amount=Decimal(amount),
                currency="USD",
                description=f"MERCHANT {i}",
                normalized_description=f"merchant {i}",
                status=TransactionStatus.CATEGORIZED,
                category_slug=category,
                category_source=CategorySource.RULES,
                occurred_at=within_current_month(1 - days_ago / 10),
            )
        )
    # Previous month, for the comparison figure.
    previous = NOW.replace(day=1) - timedelta(days=5)
    session.add(
        Transaction(
            account_id="acct_test",
            external_tx_id="ins_prev",
            amount=Decimal("-100.00"),
            currency="USD",
            description="LAST MONTH",
            normalized_description="last month",
            status=TransactionStatus.CATEGORIZED,
            category_slug="shopping",
            category_source=CategorySource.RULES,
            occurred_at=previous,
        )
    )
    await session.commit()


async def test_summary_totals_only_count_outflows(spending, make_client):
    from services.insights.main import app

    client = await make_client(app)
    body = (await client.get("/insights/summary")).json()

    assert Decimal(body["total_spend"]) == Decimal("250.00")
    categories = {row["category"]: Decimal(row["amount"]) for row in body["by_category"]}
    assert categories == {"Groceries": Decimal("150.00"), "Dining & Coffee": Decimal("100.00")}


async def test_summary_falls_back_to_a_template_without_an_llm(spending, make_client):
    from services.insights.main import app

    app.state.summarizer = None
    client = await make_client(app)
    body = (await client.get("/insights/summary")).json()

    assert body["summary_source"] == "fallback"
    assert "250" in body["summary"]


async def test_summary_uses_the_llm_when_configured(spending, make_client):
    from services.insights.main import app

    app.state.summarizer = _summarizer("__draft__")
    client = await make_client(app)
    body = (await client.get("/insights/summary")).json()

    assert body["summary_source"] == "llm"
    assert "$250.00" in body["summary"] and "Groceries ($150.00)" in body["summary"]
    app.state.summarizer = None


async def test_llm_only_ever_sees_aggregates(spending, make_client):
    from services.insights.main import app

    summarizer = _summarizer(json.dumps({"summary": "ok"}))
    app.state.summarizer = summarizer
    client = await make_client(app)
    await client.get("/insights/summary")

    sent = summarizer.client.completer.last_payload
    assert "Groceries" in sent
    # No transaction ids, external ids or raw descriptions may appear in the prompt.
    assert "ins_" not in sent
    assert "MERCHANT" not in sent
    app.state.summarizer = None


async def test_summary_falls_back_when_the_provider_fails(spending, make_client):
    from services.insights.main import app

    app.state.summarizer = _summarizer(TimeoutError("provider down"))
    client = await make_client(app)
    body = (await client.get("/insights/summary")).json()

    # Numbers are still exact; only the sentence degrades.
    assert body["summary_source"] == "fallback"
    assert Decimal(body["total_spend"]) == Decimal("250.00")
    app.state.summarizer = None


async def test_repeated_summary_requests_hit_the_cache(spending, make_client):
    from services.insights.main import app

    summarizer = _summarizer("__draft__")
    app.state.summarizer = summarizer
    client = await make_client(app)

    await client.get("/insights/summary")
    second = (await client.get("/insights/summary")).json()

    assert summarizer.client.completer.calls == 1
    assert second["summary_source"] == "cache"
    app.state.summarizer = None


@pytest.mark.parametrize(
    "changed",
    [
        {"total_spend": 60.62},
        {"currency": "EUR"},
        {"period": "2026-09"},
        {"by_category": [{"category": "Dining & Coffee", "amount": 59.62}]},
        {"change_vs_previous_pct": 5.0},
    ],
)
async def test_different_report_aggregates_do_not_reuse_a_stale_summary(changed):
    aggregates = {
        "by_category": [{"category": "Groceries", "amount": 59.62}],
        "currency": "USD",
        "total_spend": 59.62,
        "period": "2026-10",
        "change_vs_previous_pct": None,
    }
    summarizer = _summarizer("__draft__")
    assert await summarizer.summarize(aggregates) == (summarizer._fallback(aggregates), "llm")

    updated = {**aggregates, **changed}
    assert await summarizer.summarize(updated) == (summarizer._fallback(updated), "llm")
    assert await summarizer.summarize(updated) == (summarizer._fallback(updated), "cache")
    assert summarizer.client.completer.calls == 2


async def test_change_vs_previous_period_is_reported(spending, make_client):
    from services.insights.main import app

    client = await make_client(app)
    body = (await client.get("/insights/summary")).json()

    # 250 this month against 100 last month.
    assert body["change_vs_previous_pct"] == 150.0


async def test_trends_returns_a_series_oldest_first(spending, make_client):
    from services.insights.main import app

    client = await make_client(app)
    body = (await client.get("/insights/trends", params={"periods": 3})).json()

    assert len(body["periods"]) == 3
    labels = [p["period"] for p in body["periods"]]
    assert labels == sorted(labels)
    assert Decimal(body["periods"][-1]["total"]) == Decimal("250.00")


async def test_another_users_account_yields_nothing(spending, make_client):
    from services.insights.main import app

    client = await make_client(app)
    body = (await client.get("/insights/summary", params={"account_id": "acct_other"})).json()

    assert Decimal(body["total_spend"]) == Decimal("0")


# --- Currency: totals are never a blend of currencies ---------------------------------


def _foreign(
    session, external_id, amount, currency, category="dining_coffee", account="acct_test", when=None
):
    session.add(
        Transaction(
            account_id=account,
            external_tx_id=external_id,
            amount=Decimal(amount),
            currency=currency,
            description="CAFE LISBOA",
            normalized_description="cafe lisboa",
            status=TransactionStatus.CATEGORIZED,
            category_slug=category,
            category_source=CategorySource.RULES,
            occurred_at=when or within_current_month(0.6),
        )
    )


async def test_summary_reports_in_the_account_currency_and_lists_the_rest(
    spending, session, make_client
):
    from services.insights.main import app

    _foreign(session, "eur_1", "-500.00", "EUR")
    await session.commit()
    client = await make_client(app)
    body = (await client.get("/insights/summary")).json()

    assert body["currency"] == "USD"
    # The EUR purchase is not added to the dollar total...
    assert Decimal(body["total_spend"]) == Decimal("250.00")
    # ...and the response says it was left out.
    assert body["other_currencies"] == ["EUR"]


async def test_summary_can_be_requested_in_another_currency(spending, session, make_client):
    from services.insights.main import app

    _foreign(session, "eur_1", "-500.00", "EUR")
    await session.commit()
    client = await make_client(app)
    body = (await client.get("/insights/summary", params={"currency": "eur"})).json()

    assert body["currency"] == "EUR"
    assert Decimal(body["total_spend"]) == Decimal("500.00")
    assert body["other_currencies"] == ["USD"]
    assert body["change_vs_previous_pct"] is None  # no EUR spending last month
    assert "€500.00" in body["summary"]


async def test_default_currency_follows_the_users_accounts(session, make_client):
    from libs.db.models import Account
    from services.insights.main import app

    account = await session.get(Account, "acct_test")
    account.currency = "EUR"
    _foreign(session, "eur_1", "-42.00", "EUR")
    await session.commit()
    client = await make_client(app)
    body = (await client.get("/insights/summary")).json()

    assert body["currency"] == "EUR"
    assert Decimal(body["total_spend"]) == Decimal("42.00")


async def test_invalid_currency_is_rejected(make_client):
    from services.insights.main import app

    client = await make_client(app)
    response = await client.get("/insights/summary", params={"currency": "dollars"})

    assert response.status_code == 422


async def test_trends_report_their_currency(spending, session, make_client):
    from services.insights.main import app

    _foreign(session, "eur_1", "-500.00", "EUR")
    await session.commit()
    client = await make_client(app)
    body = (await client.get("/insights/trends", params={"periods": 2})).json()

    assert body["currency"] == "USD"
    assert Decimal(body["periods"][-1]["total"]) == Decimal("250.00")


async def test_summary_end_bound_matches_transaction_reporting_window(session, make_client):
    from services.insights.main import app

    reference = within_current_month(0.5)
    for external, occurred, amount in [
        ("before_end", within_current_month(0.2), "-20.00"),
        ("after_end", within_current_month(0.8), "-900.00"),
    ]:
        _foreign(session, external, amount, "USD", when=occurred)
    await session.commit()
    client = await make_client(app)
    body = (await client.get("/insights/summary", params={"as_of": reference.isoformat()})).json()
    assert Decimal(body["total_spend"]) == Decimal("20.00")


@pytest.mark.parametrize("as_of", ["2026-01-01T12:00:00", "2099-01-01T12:00:00Z"])
async def test_summary_rejects_naive_or_future_end_bound(make_client, as_of):
    from services.insights.main import app

    client = await make_client(app)
    response = await client.get("/insights/summary", params={"as_of": as_of})
    assert response.status_code == 422


async def test_summary_normalizes_offset_end_bound_to_utc(session, make_client):
    from services.insights.main import app

    _foreign(session, "september", "-20.00", "USD", when=datetime(2026, 9, 15, tzinfo=UTC))
    _foreign(session, "october", "-900.00", "USD", when=datetime(2026, 10, 1, tzinfo=UTC))
    await session.commit()
    client = await make_client(app)
    body = (
        await client.get("/insights/summary", params={"as_of": "2026-10-01T00:30:00+05:30"})
    ).json()
    assert body["period"] == "2026-09"
    assert Decimal(body["total_spend"]) == Decimal("20.00")
    assert datetime.fromisoformat(body["date_from"]) == datetime(2026, 9, 1, tzinfo=UTC)
    assert datetime.fromisoformat(body["date_to"]) == datetime(2026, 9, 30, 19, tzinfo=UTC)
