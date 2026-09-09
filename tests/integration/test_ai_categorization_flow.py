"""The Phase 3 promise, end to end: AI categorization with a working fallback."""

import json
from decimal import Decimal

import pytest

from libs.common.classification import Classification
from libs.llm_client.cache import ClassificationCache, InMemoryCache
from libs.llm_client.categorization import LLMCategorizer
from libs.llm_client.client import LLMClient
from libs.llm_client.rate_limit import InMemoryRateLimiter
from services.transactions import service
from services.transactions.schemas import TransactionCreate
from tests.conftest import TEST_USER_ID


class ScriptedCompleter:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def complete(self, *, messages, model, timeout, max_tokens):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _categorizer(reply):
    return LLMCategorizer(
        LLMClient(ScriptedCompleter(reply), max_attempts=1),
        cache=ClassificationCache(InMemoryCache()),
        rate_limiter=InMemoryRateLimiter(per_minute=600),
    )


@pytest.fixture
def payload():
    return {
        "account_id": "acct_test",
        "external_tx_id": "ai_1",
        "amount": -18.40,
        "currency": "USD",
        "description": "SQ *NEIGHBOURHOOD BAKERY",
        "occurred_at": "2026-09-05T14:30:00Z",
    }


async def test_llm_category_is_used_and_marked_as_llm(app, client, session, payload):
    # The rules engine does not know this merchant; the model does.
    app.state.classifier = _categorizer(
        json.dumps({"category": "dining_coffee", "confidence": 0.88})
    )
    created = (await client.post("/transactions", json=payload)).json()

    fetched = (await client.get(f"/transactions/{created['id']}")).json()
    assert fetched["category"] == "Dining & Coffee"
    assert fetched["category_source"] == "llm"


async def test_provider_outage_falls_back_to_rules(app, client, session, payload):
    app.state.classifier = _categorizer(TimeoutError("provider down"))
    created = (
        await client.post(
            "/transactions", json={**payload, "description": "STARBUCKS #77 SEATTLE WA"}
        )
    ).json()

    fetched = (await client.get(f"/transactions/{created['id']}")).json()
    # Degraded quality, never degraded availability.
    assert fetched["status"] == "categorized"
    assert fetched["category"] == "Dining & Coffee"
    assert fetched["category_source"] == "rules"


async def test_repeated_merchants_hit_the_cache_not_the_provider(session):
    categorizer = _categorizer(json.dumps({"category": "groceries", "confidence": 0.9}))
    completer = categorizer.client.completer

    for i in range(4):
        result = await service.ingest_transaction(
            session,
            payload=TransactionCreate(
                account_id="acct_test",
                external_tx_id=f"cache_{i}",
                amount=Decimal("-31.20"),
                currency="USD",
                description=f"TRADER JOE'S #{100 + i} SEATTLE WA",
                occurred_at="2026-09-05T14:30:00Z",
            ),
            user_id=TEST_USER_ID,
        )
        await session.commit()
        await service.categorize_transaction(session, result.transaction.id, classifier=categorizer)

    assert completer.calls == 1  # one provider call for four branch locations
    assert categorizer.cache.hit_rate == 0.75


async def test_user_correction_clears_the_cached_answer(app, client, session, payload):
    categorizer = _categorizer(json.dumps({"category": "shopping", "confidence": 0.6}))
    app.state.classifier = categorizer

    created = (await client.post("/transactions", json=payload)).json()
    assert (await categorizer.cache.get("neighbourhood bakery")) is not None

    await client.patch(f"/transactions/{created['id']}", json={"category": "Dining & Coffee"})

    assert await categorizer.cache.get("neighbourhood bakery") is None


async def test_classifier_returning_a_bad_category_still_categorizes(session):
    categorizer = _categorizer(json.dumps({"category": "yachts", "confidence": 0.99}))
    result = await service.ingest_transaction(
        session,
        payload=TransactionCreate(
            account_id="acct_test",
            external_tx_id="bad_1",
            amount=Decimal("-9.99"),
            currency="USD",
            description="NETFLIX.COM",
            occurred_at="2026-09-05T14:30:00Z",
        ),
        user_id=TEST_USER_ID,
    )
    await session.commit()

    tx = await service.categorize_transaction(
        session, result.transaction.id, classifier=categorizer
    )

    assert tx.category_slug == "subscriptions"
    assert tx.category_source.value == "rules"


def test_classification_shape_is_shared_by_both_classifiers():
    from services.transactions.categorizer import classify

    assert isinstance(classify("starbucks", Decimal("-5")), Classification)
