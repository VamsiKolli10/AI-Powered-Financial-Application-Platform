"""Builds the categorizer a service uses, from settings.

Returns None when the LLM is disabled or unconfigured, which is the signal for callers
to run on the rules engine alone - the mode the whole platform must still work in.
"""

from __future__ import annotations

from typing import Any

from libs.common.config import Settings
from libs.common.logging import get_logger
from libs.llm_client.assistant import AssistantResponder
from libs.llm_client.breaker import CircuitBreaker
from libs.llm_client.cache import ClassificationCache, InMemoryCache
from libs.llm_client.categorization import LLMCategorizer
from libs.llm_client.client import LLMClient, OpenAIChatCompleter
from libs.llm_client.prompts import CATEGORIZE_PROMPT_VERSION
from libs.llm_client.prompts.summarize_v1 import SUMMARIZE_PROMPT_VERSION
from libs.llm_client.rate_limit import InMemoryRateLimiter, TokenBucketRateLimiter
from libs.llm_client.summarize import SpendingSummarizer

log = get_logger("llm_client.factory")


def build_categorizer(
    settings: Settings, *, redis_client: Any | None = None
) -> LLMCategorizer | None:
    if not settings.llm_enabled:
        log.info("llm_disabled_using_rules_only")
        return None
    if not settings.openai_api_key:
        log.warning("llm_enabled_but_no_api_key_using_rules_only")
        return None

    cache = ClassificationCache(
        redis_client if redis_client is not None else InMemoryCache(),
        ttl_seconds=settings.llm_cache_ttl_seconds,
        prompt_version=CATEGORIZE_PROMPT_VERSION,
    )
    limiter: TokenBucketRateLimiter | InMemoryRateLimiter = (
        TokenBucketRateLimiter(redis_client, per_minute=settings.llm_rate_limit_per_minute)
        if redis_client is not None
        else InMemoryRateLimiter(per_minute=settings.llm_rate_limit_per_minute)
    )
    client = LLMClient(
        OpenAIChatCompleter(settings.openai_api_key, base_url=settings.llm_base_url),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    log.info("llm_categorizer_ready", model=settings.llm_model, base_url=settings.llm_base_url)
    return LLMCategorizer(client, cache=cache, rate_limiter=limiter, breaker=CircuitBreaker())


def build_summarizer(
    settings: Settings, *, redis_client: Any | None = None
) -> SpendingSummarizer | None:
    """Summarizer for the Insights service, or None to use the deterministic template."""
    if not settings.llm_enabled or not settings.openai_api_key:
        log.info("summarizer_disabled_using_template")
        return None

    cache = ClassificationCache(
        redis_client if redis_client is not None else InMemoryCache(),
        ttl_seconds=settings.llm_cache_ttl_seconds,
        # Summaries are cached under their own namespace and prompt version.
        prompt_version=f"summary-{SUMMARIZE_PROMPT_VERSION}",
    )
    limiter: TokenBucketRateLimiter | InMemoryRateLimiter = (
        TokenBucketRateLimiter(
            redis_client, key="llm:ratelimit:summary", per_minute=settings.llm_rate_limit_per_minute
        )
        if redis_client is not None
        else InMemoryRateLimiter(per_minute=settings.llm_rate_limit_per_minute)
    )
    client = LLMClient(
        OpenAIChatCompleter(settings.openai_api_key, base_url=settings.llm_base_url),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=400,
    )
    log.info("summarizer_ready", model=settings.llm_model)
    return SpendingSummarizer(client, cache=cache, rate_limiter=limiter)


def build_responder(
    settings: Settings, *, redis_client: Any | None = None
) -> AssistantResponder | None:
    """Assistant responder, or None to answer with the deterministic draft only."""
    if not settings.llm_enabled or not settings.openai_api_key:
        log.info("responder_disabled_using_draft_answers")
        return None

    limiter: TokenBucketRateLimiter | InMemoryRateLimiter = (
        TokenBucketRateLimiter(
            redis_client,
            key="llm:ratelimit:assistant",
            per_minute=settings.llm_rate_limit_per_minute,
        )
        if redis_client is not None
        else InMemoryRateLimiter(per_minute=settings.llm_rate_limit_per_minute)
    )
    client = LLMClient(
        OpenAIChatCompleter(settings.openai_api_key, base_url=settings.llm_base_url),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=300,
    )
    log.info("responder_ready", model=settings.llm_model)
    return AssistantResponder(client, rate_limiter=limiter)
