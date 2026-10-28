"""Deterministic answers built from retrieved figures.

This module does two jobs:
  1. It is the answer when no LLM is configured or the provider is down.
  2. Its output is the *facts block* handed to the model, which is told to rephrase
     those numbers and invent nothing.

Keeping the numbers here rather than in the prompt is what makes the assistant
grounded: the arithmetic is done in Python against the ledger, and the model only ever
does the wording.
"""

from __future__ import annotations

import re
from decimal import Decimal

from libs.common.money import format_money
from services.assistant.retrieval import RetrievedContext


def _pct_change(current: Decimal, previous: Decimal) -> str | None:
    if previous <= 0:
        return None
    change = (current - previous) / previous * 100
    if abs(change) < 1:
        return "about the same as the previous period"
    direction = "more" if change > 0 else "less"
    return f"{abs(change):.0f}% {direction} than the previous period"


def build_facts(context: RetrievedContext) -> dict:
    """Compact, JSON-safe view of the retrieved figures, for the prompt."""
    facts: dict = {
        "period": context.period_label,
        "currency": context.currency,
        "other_currencies": context.other_currencies,
        "total_spend": float(context.total_spend),
        "by_category": [
            {"category": name, "amount": float(amount), "transactions": count}
            for name, amount, count in context.by_category[:8]
        ],
    }
    if context.category_name is not None:
        facts["focus_category"] = {
            "category": context.category_name,
            "amount": float(context.category_total or 0),
            "transactions": context.category_count,
        }
        if context.comparison_total is not None:
            facts["previous_period_amount"] = float(context.comparison_total)
    if context.largest:
        facts["largest_transactions"] = [
            {"merchant": merchant, "amount": float(amount), "date": occurred.date().isoformat()}
            for merchant, amount, occurred in context.largest
        ]
    return facts


def build_answer(context: RetrievedContext, *, question: str = "") -> str:
    """Qualify partial reports so other-currency spending never appears absent."""
    text = question.strip().lower()
    if re.fullmatch(r"(?:hi|hello|hey|help)[\s!.,?]*", text) or re.search(
        r"\bwhat can you (?:do|help me with)\b", text
    ):
        return (
            "I can summarize your spending, compare periods, and find your largest purchases "
            "using transactions in your workspace. Ask a question such as "
            "‘How much did I spend on groceries last month?’"
        )
    return with_currency_scope(_build_answer(context), context)


def with_currency_scope(answer: str, context: RetrievedContext) -> str:
    """Keep the scope disclosure deterministic even when the model omits it."""
    if context.other_currencies:
        note = (
            f" This covers {context.currency} only. Spending in "
            f"{', '.join(context.other_currencies)} is not included."
        )
        if note.strip() not in answer:
            answer += note
    return answer


def _build_answer(context: RetrievedContext) -> str:
    """A correct, plain answer with no model involved."""

    def money(amount: Decimal) -> str:
        return format_money(amount, context.currency)

    if not context.has_data:
        if context.other_currencies:
            return f"I don't see any {context.currency} spending for {context.period_label}."
        return (
            f"I don't see any transactions for {context.period_label}. "
            "Once you add transactions, I can explain your spending and compare periods."
        )

    if context.largest:
        merchant, amount, occurred = context.largest[0]
        others = "".join(
            f" Then {m} at {money(a)} on {o:%-d %B}." for m, a, o in context.largest[1:3]
        )
        category = f" on {context.category_name}" if context.category_name else ""
        return (
            f"Your largest transaction{category} in {context.period_label} was {money(amount)} "
            f"at {merchant} on {occurred:%-d %B}.{others}"
        )

    if context.category_name is not None:
        total = context.category_total or Decimal("0")
        if total <= 0:
            return (
                f"You don't have any {context.category_name} spending recorded for "
                f"{context.period_label}."
            )
        sentence = (
            f"You spent {money(total)} on {context.category_name} in "
            f"{context.period_label}, across {context.category_count} "
            f"transaction{'s' if context.category_count != 1 else ''}."
        )
        if context.comparison_total is not None:
            change = _pct_change(total, context.comparison_total)
            if change:
                sentence += f" That's {change}."
        return sentence

    leaders = ", ".join(
        f"{name} ({money(amount)})" for name, amount, _count in context.by_category[:3]
    )
    return (
        f"You spent {money(context.total_spend)} in {context.period_label}. "
        f"The largest categories were {leaders}."
    )
