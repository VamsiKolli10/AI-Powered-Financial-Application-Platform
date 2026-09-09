"""LLM-backed transaction categorizer.

Order of operations for every call:
    cache -> rate limiter -> circuit breaker -> provider -> validate -> cache

Any failure raises an `LLMError`; the Transactions service catches it and falls back to
the deterministic rules engine, so a provider outage degrades quality, never
availability (ARCHITECTURE.md sec. 6).
"""

from __future__ import annotations

import time
from decimal import Decimal

from libs.common.classification import Classification
from libs.common.logging import get_logger
from libs.llm_client.breaker import CircuitBreaker
from libs.llm_client.cache import ClassificationCache
from libs.llm_client.client import LLMClient
from libs.llm_client.errors import LLMInvalidResponse, LLMRateLimited, LLMUnavailable
from libs.llm_client.metrics import CATEGORIZATIONS, LLM_CACHE, LLM_CALLS, LLM_LATENCY
from libs.llm_client.prompts import build_categorize_messages
from libs.llm_client.rate_limit import InMemoryRateLimiter, TokenBucketRateLimiter

log = get_logger("llm_client.categorization")

Limiter = TokenBucketRateLimiter | InMemoryRateLimiter


class LLMCategorizer:
    def __init__(
        self,
        client: LLMClient,
        *,
        cache: ClassificationCache,
        rate_limiter: Limiter,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.client = client
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.breaker = breaker or CircuitBreaker()

    async def __call__(self, normalized_description: str, amount: Decimal) -> Classification:
        """Signature matches `services.transactions.service.Classifier`."""
        cached = await self.cache.get(normalized_description)
        if cached is not None:
            LLM_CACHE.labels("hit").inc()
            CATEGORIZATIONS.labels("cache").inc()
            return Classification(
                category_slug=cached["category"],
                confidence=float(cached.get("confidence", 0.0)),
                source="llm",
            )
        LLM_CACHE.labels("miss").inc()

        if not self.breaker.allows_request():
            LLM_CALLS.labels("breaker_open").inc()
            raise LLMUnavailable("Circuit breaker is open.")

        allowed, retry_after = await self.rate_limiter.acquire()
        if not allowed:
            LLM_CALLS.labels("rate_limited").inc()
            raise LLMRateLimited(f"LLM rate limit reached; retry in {retry_after}s.")

        messages = build_categorize_messages(normalized_description, f"{amount:.2f}")
        started = time.perf_counter()
        try:
            payload = await self.client.complete_json(messages)
        except LLMUnavailable:
            self.breaker.record_failure()
            LLM_CALLS.labels("error").inc()
            raise
        except LLMInvalidResponse:
            # A malformed reply is the model's fault, not an outage: do not trip the breaker.
            LLM_CALLS.labels("invalid").inc()
            raise
        finally:
            LLM_LATENCY.observe(time.perf_counter() - started)

        self.breaker.record_success()
        LLM_CALLS.labels("success").inc()
        result = _parse(payload)

        await self.cache.set(
            normalized_description,
            {"category": result.category_slug, "confidence": result.confidence},
        )
        CATEGORIZATIONS.labels("llm").inc()
        log.info(
            "llm_categorized",
            merchant=normalized_description,
            category=result.category_slug,
            confidence=result.confidence,
        )
        return result

    async def invalidate(self, normalized_description: str) -> None:
        """A user corrected this merchant: drop the cached answer."""
        await self.cache.invalidate(normalized_description)


def _parse(payload: dict) -> Classification:
    """Validate the model's reply against the canonical taxonomy."""
    from libs.db.categories import CATEGORY_SLUGS

    slug = str(payload.get("category", "")).strip().lower()
    if slug not in CATEGORY_SLUGS:
        raise LLMInvalidResponse(f"LLM returned unknown category '{slug}'.")
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return Classification(
        category_slug=slug, confidence=max(0.0, min(confidence, 1.0)), source="llm"
    )
