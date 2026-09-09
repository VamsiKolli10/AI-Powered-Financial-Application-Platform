"""LLM client behaviour: retries, JSON handling, cache, rate limiting, breaker."""

import asyncio
import json
from decimal import Decimal

import pytest

from libs.llm_client.breaker import BreakerState, CircuitBreaker
from libs.llm_client.cache import ClassificationCache, InMemoryCache
from libs.llm_client.categorization import LLMCategorizer
from libs.llm_client.client import LLMClient
from libs.llm_client.errors import LLMInvalidResponse, LLMRateLimited, LLMUnavailable
from libs.llm_client.rate_limit import InMemoryRateLimiter


class FakeCompleter:
    """Scripted provider: each item is either a reply string or an exception to raise."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0
        self.last_messages: list[dict] | None = None

    async def complete(self, *, messages, model, timeout, max_tokens):
        self.calls += 1
        self.last_messages = messages
        item = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def _ok(category="dining_coffee", confidence=0.93):
    return json.dumps({"category": category, "confidence": confidence})


def build(completer, *, cache=None, limiter=None, breaker=None):
    return LLMCategorizer(
        LLMClient(completer, max_attempts=3),
        cache=cache or ClassificationCache(InMemoryCache()),
        rate_limiter=limiter or InMemoryRateLimiter(per_minute=600),
        breaker=breaker or CircuitBreaker(),
    )


class TestLLMClient:
    async def test_parses_a_json_reply(self):
        client = LLMClient(FakeCompleter(_ok()))
        assert (await client.complete_json([]))["category"] == "dining_coffee"

    async def test_retries_transient_failures_then_succeeds(self):
        completer = FakeCompleter(TimeoutError("timeout"), _ok())
        client = LLMClient(completer, max_attempts=3)
        await client.complete_json([])
        assert completer.calls == 2

    async def test_gives_up_after_max_attempts(self):
        completer = FakeCompleter(TimeoutError("timeout"))
        with pytest.raises(LLMUnavailable):
            await LLMClient(completer, max_attempts=3).complete_json([])
        assert completer.calls == 3

    async def test_non_json_reply_is_an_invalid_response(self):
        with pytest.raises(LLMInvalidResponse):
            await LLMClient(FakeCompleter("I think it's coffee!")).complete_json([])


class TestCategorizer:
    async def test_returns_llm_classification(self):
        categorizer = build(FakeCompleter(_ok()))
        result = await categorizer("starbucks", Decimal("-6.75"))
        assert result.category_slug == "dining_coffee"
        assert result.source == "llm"

    async def test_second_call_for_same_merchant_is_served_from_cache(self):
        completer = FakeCompleter(_ok())
        cache = ClassificationCache(InMemoryCache())
        categorizer = build(completer, cache=cache)

        await categorizer("starbucks", Decimal("-6.75"))
        await categorizer("starbucks", Decimal("-4.10"))

        assert completer.calls == 1
        assert cache.hits == 1 and cache.hit_rate == 0.5

    async def test_unknown_category_is_rejected(self):
        categorizer = build(FakeCompleter(_ok(category="yachts")))
        with pytest.raises(LLMInvalidResponse):
            await categorizer("mystery vendor", Decimal("-10"))

    async def test_confidence_is_clamped(self):
        categorizer = build(FakeCompleter(_ok(confidence=7.5)))
        result = await categorizer("starbucks", Decimal("-6.75"))
        assert result.confidence == 1.0

    async def test_rate_limit_raises_before_calling_the_provider(self):
        completer = FakeCompleter(_ok())
        limiter = InMemoryRateLimiter(per_minute=60, burst=1)
        categorizer = build(completer, limiter=limiter)

        await categorizer("starbucks", Decimal("-6.75"))
        with pytest.raises(LLMRateLimited):
            await categorizer("trader joe's", Decimal("-52.10"))
        assert completer.calls == 1

    async def test_invalid_response_does_not_trip_the_breaker(self):
        breaker = CircuitBreaker(failure_threshold=2)
        categorizer = build(FakeCompleter(_ok(category="yachts")), breaker=breaker)
        for _ in range(3):
            with pytest.raises(LLMInvalidResponse):
                await categorizer("mystery vendor", Decimal("-10"))
        assert breaker.state is BreakerState.CLOSED

    async def test_breaker_opens_after_repeated_outages_and_stops_calling(self):
        completer = FakeCompleter(TimeoutError("down"))
        breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=60)
        categorizer = build(completer, breaker=breaker)

        for _ in range(2):
            with pytest.raises(LLMUnavailable):
                await categorizer("starbucks", Decimal("-6.75"))
        calls_before = completer.calls

        with pytest.raises(LLMUnavailable):
            await categorizer("starbucks", Decimal("-6.75"))

        assert breaker.state is BreakerState.OPEN
        assert completer.calls == calls_before  # refused without touching the provider

    async def test_breaker_recovers_after_the_reset_window(self):
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=0.05)
        # Three failures exhaust the client's retries and trip the breaker; the
        # fourth provider call is the half-open probe.
        completer = FakeCompleter(
            TimeoutError("down"), TimeoutError("down"), TimeoutError("down"), _ok()
        )
        categorizer = build(completer, breaker=breaker)

        with pytest.raises(LLMUnavailable):
            await categorizer("starbucks", Decimal("-6.75"))
        assert breaker.state is BreakerState.OPEN

        await asyncio.sleep(0.06)
        result = await categorizer("starbucks", Decimal("-6.75"))

        assert result.category_slug == "dining_coffee"
        assert breaker.state is BreakerState.CLOSED

    async def test_user_correction_invalidates_the_cached_merchant(self):
        completer = FakeCompleter(_ok())
        categorizer = build(completer)

        await categorizer("starbucks", Decimal("-6.75"))
        await categorizer.invalidate("starbucks")
        await categorizer("starbucks", Decimal("-6.75"))

        assert completer.calls == 2

    async def test_prompt_never_receives_a_raw_description(self):
        completer = FakeCompleter(_ok())
        categorizer = build(completer)
        await categorizer("starbucks", Decimal("-6.75"))

        sent = json.dumps(completer.last_messages)
        assert "starbucks" in sent
        assert "1234" not in sent  # store numbers are stripped upstream


class TestCacheResilience:
    async def test_cache_read_failure_is_not_fatal(self):
        class BrokenBackend:
            async def get(self, key):
                raise ConnectionError("redis down")

            async def set(self, key, value, ex=None):
                raise ConnectionError("redis down")

            async def delete(self, *keys):
                raise ConnectionError("redis down")

        categorizer = build(FakeCompleter(_ok()), cache=ClassificationCache(BrokenBackend()))
        result = await categorizer("starbucks", Decimal("-6.75"))
        assert result.category_slug == "dining_coffee"

    async def test_corrupt_cache_entry_is_ignored(self):
        backend = InMemoryCache()
        cache = ClassificationCache(backend)
        await backend.set(cache.key_for("starbucks"), "not json")
        assert await cache.get("starbucks") is None


class TestRateLimiter:
    async def test_bucket_refills_over_time(self):
        limiter = InMemoryRateLimiter(per_minute=6000, burst=1)
        assert (await limiter.acquire())[0] is True
        assert (await limiter.acquire())[0] is False
        await asyncio.sleep(0.05)
        assert (await limiter.acquire())[0] is True

    async def test_refusal_reports_retry_after(self):
        limiter = InMemoryRateLimiter(per_minute=60, burst=1)
        await limiter.acquire()
        allowed, retry_after = await limiter.acquire()
        assert allowed is False and retry_after >= 1


class TestRateLimiterFailureModes:
    """Fail-open is right for the LLM client and wrong for the edge, so it is a choice."""

    class BrokenBackend:
        async def eval(self, *args, **kwargs):
            raise ConnectionError("redis down")

    async def test_fail_open_admits_the_request(self):
        from libs.llm_client.rate_limit import TokenBucketRateLimiter

        limiter = TokenBucketRateLimiter(self.BrokenBackend(), per_minute=10)
        assert await limiter.acquire() == (True, 0)

    async def test_fail_closed_raises_so_the_caller_can_degrade(self):
        import pytest

        from libs.llm_client.rate_limit import RateLimiterUnavailable, TokenBucketRateLimiter

        limiter = TokenBucketRateLimiter(self.BrokenBackend(), per_minute=10, fail_open=False)
        with pytest.raises(RateLimiterUnavailable):
            await limiter.acquire()
