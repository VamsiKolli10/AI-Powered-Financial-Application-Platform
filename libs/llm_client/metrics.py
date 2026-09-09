"""Prometheus metrics for the AI path - cache hit rate and fallbacks are the ones to watch."""

from prometheus_client import Counter, Histogram

LLM_CACHE = Counter("llm_cache_events_total", "Classification cache outcomes", ["result"])
LLM_CALLS = Counter("llm_calls_total", "LLM provider calls", ["outcome"])
LLM_LATENCY = Histogram("llm_call_duration_seconds", "LLM provider call latency")
CATEGORIZATIONS = Counter("categorizations_total", "Categorizations by source", ["source"])
