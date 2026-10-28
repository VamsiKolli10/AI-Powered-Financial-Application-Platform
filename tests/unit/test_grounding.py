"""Provider content must not replace ledger-derived figures."""

import hashlib
import json

import pytest

from libs.llm_client.assistant import AssistantResponder
from libs.llm_client.cache import ClassificationCache, InMemoryCache
from libs.llm_client.client import LLMClient
from libs.llm_client.grounding import valid_rewrite
from libs.llm_client.rate_limit import InMemoryRateLimiter
from libs.llm_client.summarize import SpendingSummarizer
from tests.unit.test_llm_client import FakeCompleter

FACTS = {
    "currency": "USD",
    "total_spend": 100,
    "by_category": [
        {"category": "Groceries", "amount": 60},
        {"category": "Dining & Coffee", "amount": 40},
    ],
}
DRAFT = "You spent $100.00 in October. Groceries ($60.00), Dining & Coffee ($40.00)."


@pytest.mark.parametrize(
    "reply",
    [
        None,
        123,
        [],
        {},
        "",
        "None",
        "You spent $9999.99 in October.",
        DRAFT.replace("$100.00", "€100.00"),
        DRAFT.replace(
            "Groceries ($60.00), Dining & Coffee ($40.00)",
            "Groceries ($40.00), Dining & Coffee ($60.00)",
        ),
        DRAFT.replace("October", "November"),
        DRAFT + " Savings increased 20%.",
        DRAFT + " You spent one million elsewhere.",
        DRAFT.replace("$100.00", "$-100.00"),
        DRAFT.replace("$100.00", "euros $100.00"),
        DRAFT.replace("$100.00", "$100.01"),
    ],
)
async def test_invalid_rewrite_returns_computed_draft(reply):
    responder = AssistantResponder(
        LLMClient(FakeCompleter(json.dumps({"reply": reply}))),
        rate_limiter=InMemoryRateLimiter(per_minute=600),
    )
    assert await responder.respond(question="Spending?", facts=FACTS, draft=DRAFT) == (
        DRAFT,
        "draft",
        "invalid_response",
    )


def test_valid_rewrite_preserves_figures_and_anchors():
    assert valid_rewrite(DRAFT.replace("You spent", "Your spending was"), draft=DRAFT, facts=FACTS)


@pytest.mark.parametrize("value", [None, {}, [], 123, "Invented $555.00."])
async def test_summary_rejects_malformed_provider_and_cached_values(value):
    cache = ClassificationCache(InMemoryCache())
    summarizer = SpendingSummarizer(
        LLMClient(FakeCompleter(json.dumps({"summary": value}))),
        cache=cache,
        rate_limiter=InMemoryRateLimiter(per_minute=600),
    )
    aggregates = {**FACTS, "period": "October"}
    key = hashlib.sha256(json.dumps(aggregates, sort_keys=True).encode()).hexdigest()
    await cache.set(key, {"summary": value})
    assert await summarizer.summarize(aggregates) == (summarizer._fallback(aggregates), "fallback")
