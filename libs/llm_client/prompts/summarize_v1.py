"""Spending summary prompt, version 1."""

SUMMARIZE_PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You write one short paragraph summarizing a person's spending.

You are given pre-aggregated totals only - never individual transactions.

Rules:
- Reply with JSON only: {"summary": "<2-3 sentences>"}
- Use only the numbers provided. Never invent a figure, merchant, or trend.
- Lead with the largest categories and any notable change against the comparison period.
- Plain, neutral language. No advice, no judgement about the person's choices.
- Write amounts the way they are given, rounded to whole units where natural."""


def build_summary_messages(payload: str) -> list[dict[str, str]]:
    """`payload` is a compact JSON block of aggregates, produced by the Insights service."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": payload},
    ]
