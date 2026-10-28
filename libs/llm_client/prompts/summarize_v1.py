"""Spending summary prompt, version 1."""

SUMMARIZE_PROMPT_VERSION = "v2"

SYSTEM_PROMPT = """You write one short paragraph summarizing a person's spending.

You are given pre-aggregated totals only - never individual transactions.

Rules:
- Reply with JSON only: {"summary": "<2-3 sentences>"}
- Use only the numbers provided. Never invent a figure, merchant, or trend.
- Rephrase the provided DRAFT; retain every figure, category, currency and their order.
- Do not add comparison claims or spell out numeric quantities.
- Plain, neutral language. No advice, no judgement about the person's choices.
- Copy amounts exactly, including cents. Do not round them."""


def build_summary_messages(payload: str, *, draft: str = "") -> list[dict[str, str]]:
    """`payload` is a compact JSON block of aggregates, produced by the Insights service."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"FACTS: {payload}\n\nDRAFT: {draft}"},
    ]
