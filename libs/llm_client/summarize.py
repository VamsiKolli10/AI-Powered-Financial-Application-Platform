"""LLM spending summaries over aggregates.

The model never sees a transaction: the Insights service aggregates first and passes
totals (ARCHITECTURE.md sec. 4 and API_DESIGN.md). Failure produces a deterministic
template sentence rather than an error, because a summary is a nice-to-have on top of
numbers that are already correct.
"""

from __future__ import annotations

import hashlib
import json

from libs.common.logging import get_logger
from libs.common.money import format_money
from libs.llm_client.cache import ClassificationCache
from libs.llm_client.client import LLMClient
from libs.llm_client.errors import LLMError
from libs.llm_client.grounding import valid_rewrite
from libs.llm_client.metrics import LLM_CACHE, LLM_CALLS
from libs.llm_client.prompts.summarize_v1 import build_summary_messages
from libs.llm_client.rate_limit import InMemoryRateLimiter, TokenBucketRateLimiter

log = get_logger("llm_client.summarize")


def fallback_summary(
    period: str, total: float, top: list[tuple[str, float]], *, currency: str = "USD"
) -> str:
    """Deterministic summary used when the LLM is unavailable or disabled."""
    if not top:
        return f"No spending recorded for {period}."
    leaders = ", ".join(f"{name} ({format_money(amount, currency)})" for name, amount in top[:3])
    return f"Total spending for {period} was {format_money(total, currency)}, led by {leaders}."


class SpendingSummarizer:
    def __init__(
        self,
        client: LLMClient,
        *,
        cache: ClassificationCache,
        rate_limiter: TokenBucketRateLimiter | InMemoryRateLimiter,
    ) -> None:
        self.client = client
        self.cache = cache
        self.rate_limiter = rate_limiter

    async def summarize(self, aggregates: dict) -> tuple[str, str]:
        """Return (summary, source) where source is "llm", "cache" or "fallback"."""
        payload = json.dumps(aggregates, sort_keys=True, default=str)
        draft = self._fallback(aggregates)
        # ClassificationCache groups merchant descriptions by their first two words.
        # Reports need every aggregate in the key, including currency and period.
        aggregate_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        cached = await self.cache.get(aggregate_key)
        if cached is not None and valid_rewrite(
            cached.get("summary"), draft=draft, facts=aggregates
        ):
            LLM_CACHE.labels("hit").inc()
            return cached["summary"], "cache"
        LLM_CACHE.labels("miss").inc()

        allowed, _retry_after = await self.rate_limiter.acquire()
        if not allowed:
            LLM_CALLS.labels("rate_limited").inc()
            return self._fallback(aggregates), "fallback"

        try:
            response = await self.client.complete_json(build_summary_messages(payload, draft=draft))
        except LLMError as exc:
            LLM_CALLS.labels("error").inc()
            log.warning("summary_llm_failed_using_fallback", error_type=type(exc).__name__)
            return self._fallback(aggregates), "fallback"

        value = response.get("summary")
        summary = value.strip() if isinstance(value, str) else ""
        if not valid_rewrite(summary, draft=draft, facts=aggregates):
            LLM_CALLS.labels("invalid").inc()
            return self._fallback(aggregates), "fallback"

        LLM_CALLS.labels("success").inc()
        await self.cache.set(aggregate_key, {"summary": summary})
        return summary, "llm"

    @staticmethod
    def _fallback(aggregates: dict) -> str:
        by_category = aggregates.get("by_category", [])
        top = [(row["category"], float(row["amount"])) for row in by_category]
        return fallback_summary(
            str(aggregates.get("period", "the period")),
            float(aggregates.get("total_spend", 0.0)),
            top,
            currency=str(aggregates.get("currency", "USD")),
        )
