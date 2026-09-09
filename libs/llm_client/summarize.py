"""LLM spending summaries over aggregates.

The model never sees a transaction: the Insights service aggregates first and passes
totals (ARCHITECTURE.md sec. 4 and API_DESIGN.md). Failure produces a deterministic
template sentence rather than an error, because a summary is a nice-to-have on top of
numbers that are already correct.
"""

from __future__ import annotations

import json

from libs.common.logging import get_logger
from libs.llm_client.cache import ClassificationCache
from libs.llm_client.client import LLMClient
from libs.llm_client.errors import LLMError
from libs.llm_client.metrics import LLM_CACHE, LLM_CALLS
from libs.llm_client.prompts.summarize_v1 import build_summary_messages
from libs.llm_client.rate_limit import InMemoryRateLimiter, TokenBucketRateLimiter

log = get_logger("llm_client.summarize")


def fallback_summary(period: str, total: float, top: list[tuple[str, float]]) -> str:
    """Deterministic summary used when the LLM is unavailable or disabled."""
    if not top:
        return f"No spending recorded for {period}."
    leaders = ", ".join(f"{name} ({amount:,.0f})" for name, amount in top[:3])
    return f"Total spending for {period} was {total:,.2f}, led by {leaders}."


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

        cached = await self.cache.get(payload)
        if cached is not None and "summary" in cached:
            LLM_CACHE.labels("hit").inc()
            return str(cached["summary"]), "cache"
        LLM_CACHE.labels("miss").inc()

        allowed, _retry_after = await self.rate_limiter.acquire()
        if not allowed:
            LLM_CALLS.labels("rate_limited").inc()
            return self._fallback(aggregates), "fallback"

        try:
            response = await self.client.complete_json(build_summary_messages(payload))
        except LLMError as exc:
            LLM_CALLS.labels("error").inc()
            log.warning("summary_llm_failed_using_fallback", error=str(exc))
            return self._fallback(aggregates), "fallback"

        summary = str(response.get("summary", "")).strip()
        if not summary:
            LLM_CALLS.labels("invalid").inc()
            return self._fallback(aggregates), "fallback"

        LLM_CALLS.labels("success").inc()
        await self.cache.set(payload, {"summary": summary})
        return summary, "llm"

    @staticmethod
    def _fallback(aggregates: dict) -> str:
        by_category = aggregates.get("by_category", [])
        top = [(row["category"], float(row["amount"])) for row in by_category]
        return fallback_summary(
            str(aggregates.get("period", "the period")),
            float(aggregates.get("total_spend", 0.0)),
            top,
        )
