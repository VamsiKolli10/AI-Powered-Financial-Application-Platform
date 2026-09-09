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
                occurred_at=NOW - timedelta(days=days_ago),
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

    app.state.summarizer = _summarizer(json.dumps({"summary": "You spent mostly on groceries."}))
    client = await make_client(app)
    body = (await client.get("/insights/summary")).json()

    assert body["summary_source"] == "llm"
    assert body["summary"] == "You spent mostly on groceries."
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

    summarizer = _summarizer(json.dumps({"summary": "cached sentence"}))
    app.state.summarizer = summarizer
    client = await make_client(app)

    await client.get("/insights/summary")
    second = (await client.get("/insights/summary")).json()

    assert summarizer.client.completer.calls == 1
    assert second["summary_source"] == "cache"
    app.state.summarizer = None


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
