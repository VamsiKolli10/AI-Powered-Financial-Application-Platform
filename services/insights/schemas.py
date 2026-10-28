"""Insights API contracts (API_DESIGN.md)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class CategoryAmount(BaseModel):
    category: str
    amount: Decimal
    transaction_count: int = 0


class SummaryResponse(BaseModel):
    period: str
    date_from: datetime
    date_to: datetime
    # ISO 4217 code every amount in this response is in.
    currency: str
    total_spend: Decimal
    by_category: list[CategoryAmount]
    summary: str
    # "llm", "cache" or "fallback" - so a reader can tell a generated sentence from a
    # deterministic one instead of guessing.
    summary_source: str
    change_vs_previous_pct: float | None = None
    # Currencies with spending in the period that are not included in the totals.
    other_currencies: list[str] = []


class TrendPoint(BaseModel):
    period: str
    total: Decimal
    by_category: list[CategoryAmount]


class TrendsResponse(BaseModel):
    currency: str
    periods: list[TrendPoint]
