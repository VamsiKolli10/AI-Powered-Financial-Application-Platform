"""Question -> grounded figures.

Lightweight retrieval, deliberately not a vector database: the questions this assistant
answers are about a user's own structured ledger, so the right retrieval is a scoped SQL
aggregate, not a similarity search. Every number the assistant states comes from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.categories import CATEGORIES, CATEGORY_NAMES
from libs.db.repositories import AccountRepository, TransactionRepository

MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}

# Words a person actually uses, mapped to canonical slugs.
_CATEGORY_HINTS: dict[str, str] = {
    "dining": "dining_coffee",
    "restaurant": "dining_coffee",
    "restaurants": "dining_coffee",
    "eating out": "dining_coffee",
    "coffee": "dining_coffee",
    "food delivery": "dining_coffee",
    "takeout": "dining_coffee",
    "grocery": "groceries",
    "groceries": "groceries",
    "supermarket": "groceries",
    "gas": "fuel",
    "petrol": "fuel",
    "fuel": "fuel",
    "charging": "fuel",
    "transport": "transport",
    "travel": "travel",
    "flights": "travel",
    "hotels": "travel",
    "rent": "housing",
    "mortgage": "housing",
    "housing": "housing",
    "utilities": "utilities",
    "internet": "utilities",
    "phone bill": "utilities",
    "shopping": "shopping",
    "clothes": "shopping",
    "electronics": "electronics",
    "gadgets": "electronics",
    "subscriptions": "subscriptions",
    "streaming": "subscriptions",
    "entertainment": "entertainment",
    "movies": "entertainment",
    "games": "entertainment",
    "health": "health",
    "pharmacy": "health",
    "gym": "health",
    "medical": "health",
    "education": "education",
    "tuition": "education",
    "courses": "education",
    "fees": "fees",
    "income": "income",
    "salary": "income",
    "paycheck": "income",
    "transfers": "transfers",
    "savings": "transfers",
}
for _slug, _name, _ in CATEGORIES:
    _CATEGORY_HINTS.setdefault(_name.lower(), _slug)


@dataclass
class Period:
    start: datetime
    end: datetime
    label: str


@dataclass
class RetrievalPlan:
    period: Period
    category_slug: str | None = None
    wants_breakdown: bool = False
    wants_largest: bool = False


@dataclass
class Source:
    type: str
    category: str | None = None
    period: str | None = None


@dataclass
class RetrievedContext:
    """Everything the answer may be built from - and nothing else."""

    period_label: str
    total_spend: Decimal
    by_category: list[tuple[str, Decimal, int]] = field(default_factory=list)
    category_name: str | None = None
    category_total: Decimal | None = None
    category_count: int = 0
    largest: list[tuple[str, Decimal, datetime]] = field(default_factory=list)
    comparison_label: str | None = None
    comparison_total: Decimal | None = None
    sources: list[Source] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return bool(self.by_category) or bool(self.largest)


def _month_start(when: datetime) -> datetime:
    return when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def parse_period(message: str, *, now: datetime | None = None) -> Period:
    """Resolve the time window a question is about. Defaults to the current month."""
    now = now or datetime.now(UTC)
    text = message.lower()

    if "last month" in text or "previous month" in text:
        end = _month_start(now)
        start = _month_start(end - timedelta(days=1))
        return Period(start, end, f"{start:%B %Y}")

    if "last week" in text:
        this_week = now - timedelta(days=now.weekday())
        start = (this_week - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        return Period(start, start + timedelta(days=7), "last week")

    if "this week" in text:
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return Period(start, now, "this week")

    if "this year" in text:
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return Period(start, now, f"{now:%Y}")

    span = re.search(r"last (\d+) (day|week|month)s?", text)
    if span:
        count, unit = int(span.group(1)), span.group(2)
        days = {"day": 1, "week": 7, "month": 30}[unit] * count
        start = now - timedelta(days=days)
        return Period(start, now, f"the last {count} {unit}{'s' if count > 1 else ''}")

    for name, number in MONTHS.items():
        if re.search(rf"\b{name}\b", text):
            year = now.year if number <= now.month else now.year - 1
            start = datetime(year, number, 1, tzinfo=UTC)
            end = (
                datetime(year + 1, 1, 1, tzinfo=UTC)
                if number == 12
                else datetime(year, number + 1, 1, tzinfo=UTC)
            )
            return Period(start, min(end, now), f"{start:%B %Y}")

    start = _month_start(now)
    return Period(start, now, f"{now:%B %Y}")


def parse_category(message: str) -> str | None:
    text = message.lower()
    # Longest hint first, so "food delivery" beats "food".
    for hint in sorted(_CATEGORY_HINTS, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(hint)}(?![a-z])", text):
            return _CATEGORY_HINTS[hint]
    return None


def plan(message: str, *, now: datetime | None = None) -> RetrievalPlan:
    text = message.lower()
    return RetrievalPlan(
        period=parse_period(message, now=now),
        category_slug=parse_category(message),
        wants_breakdown=any(
            w in text for w in ("breakdown", "categories", "what did i spend on", "where did")
        ),
        wants_largest=any(
            w in text for w in ("largest", "biggest", "most expensive", "top ", "priciest")
        ),
    )


async def retrieve(
    session: AsyncSession, *, user_id: str, message: str, now: datetime | None = None
) -> RetrievedContext:
    """Fetch exactly the figures needed to answer, scoped to this user's accounts."""
    now = now or datetime.now(UTC)
    retrieval = plan(message, now=now)
    period = retrieval.period

    account_ids = await AccountRepository(session).ids_for_user(user_id)
    transactions = TransactionRepository(session)

    totals = await transactions.spend_by_category(
        account_ids=account_ids, date_from=period.start, date_to=period.end
    )
    by_category = [
        (CATEGORY_NAMES.get(slug or "", "Uncategorized"), amount, count)
        for slug, amount, count in totals
    ]
    context = RetrievedContext(
        period_label=period.label,
        total_spend=sum((amount for _n, amount, _c in by_category), Decimal("0")),
        by_category=by_category,
        sources=[Source(type="aggregate", period=period.label)],
    )

    if retrieval.category_slug:
        name = CATEGORY_NAMES.get(retrieval.category_slug, retrieval.category_slug)
        context.category_name = name
        context.category_total = Decimal("0")
        for slug, amount, count in totals:
            if slug == retrieval.category_slug:
                context.category_total = amount
                context.category_count = count
        context.sources = [Source(type="aggregate", category=name, period=period.label)]

        # A category question usually invites "...compared to what?"
        span = period.end - period.start
        prev_totals = await transactions.spend_by_category(
            account_ids=account_ids,
            date_from=period.start - span,
            date_to=period.start,
        )
        for slug, amount, _count in prev_totals:
            if slug == retrieval.category_slug:
                context.comparison_label = "the previous period"
                context.comparison_total = amount

    if retrieval.wants_largest:
        rows, _cursor = await transactions.list_page(
            account_ids=account_ids,
            limit=100,
            date_from=period.start,
            date_to=period.end,
            category=retrieval.category_slug,
        )
        outflows = sorted((t for t in rows if t.amount < 0), key=lambda t: t.amount)[:3]
        context.largest = [
            (t.merchant or t.normalized_description, abs(t.amount), t.occurred_at) for t in outflows
        ]
        context.sources.append(Source(type="transactions", period=period.label))

    return context
