"""The classification result shared by the rules engine and the LLM client."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Classification:
    category_slug: str
    confidence: float
    source: str  # "rules" | "llm"
