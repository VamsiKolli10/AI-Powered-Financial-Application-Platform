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

from decimal import Decimal

from services.assistant.retrieval import RetrievedContext


def _money(amount: Decimal) -> str:
    return f"${amount:,.2f}"


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


def build_answer(context: RetrievedContext) -> str:
    """A correct, plain answer with no model involved."""
    if not context.has_data:
        return f"I don't see any transactions for {context.period_label}."

    if context.category_name is not None:
        total = context.category_total or Decimal("0")
        if total <= 0:
            return (
                f"You don't have any {context.category_name} spending recorded for "
                f"{context.period_label}."
            )
        sentence = (
            f"You spent {_money(total)} on {context.category_name} in "
            f"{context.period_label}, across {context.category_count} "
            f"transaction{'s' if context.category_count != 1 else ''}."
        )
        if context.comparison_total is not None:
            change = _pct_change(total, context.comparison_total)
            if change:
                sentence += f" That's {change}."
        return sentence

    if context.largest:
        merchant, amount, occurred = context.largest[0]
        others = "".join(
            f" Then {m} at {_money(a)} on {o:%-d %B}." for m, a, o in context.largest[1:3]
        )
        return (
            f"Your largest transaction in {context.period_label} was {_money(amount)} "
            f"at {merchant} on {occurred:%-d %B}.{others}"
        )

    leaders = ", ".join(
        f"{name} ({_money(amount)})" for name, amount, _count in context.by_category[:3]
    )
    return (
        f"You spent {_money(context.total_spend)} in {context.period_label}. "
        f"The largest categories were {leaders}."
    )
