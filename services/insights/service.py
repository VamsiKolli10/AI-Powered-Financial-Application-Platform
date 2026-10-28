"""Aggregation for the Insights service.

Aggregates are computed from PostgreSQL, the system of record, rather than maintained
incrementally from events. Two reasons: a derived counter can drift from the ledger and
a financial figure that disagrees with the transactions behind it is worse than a slow
query; and these aggregates are indexed group-bys, not a heavy job. The expensive part
is the LLM sentence on top, and that is what gets cached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.categories import CATEGORY_NAMES
from libs.db.repositories import AccountRepository, TransactionRepository
from services.insights.periods import period_bounds, previous_bounds
from services.insights.schemas import CategoryAmount, TrendPoint


@dataclass
class SummaryResult:
    aggregates: dict
    rows: list[CategoryAmount]
    total_spend: Decimal
    change_pct: float | None
    currency: str
    date_from: datetime
    date_to: datetime
    # Currencies with spending in the period that the totals above do not include.
    other_currencies: list[str] = field(default_factory=list)


async def _account_ids(session: AsyncSession, user_id: str, account_id: str | None) -> list[str]:
    accounts = AccountRepository(session)
    if account_id:
        account = await accounts.get(account_id)
        if account is None or account.user_id != user_id:
            return []
        return [account_id]
    return await accounts.ids_for_user(user_id)


def _to_rows(totals: list[tuple[str | None, Decimal, int]]) -> list[CategoryAmount]:
    return [
        CategoryAmount(
            category=CATEGORY_NAMES.get(slug or "", "Uncategorized"),
            amount=amount,
            transaction_count=count,
        )
        for slug, amount, count in totals
    ]


async def resolve_currency(
    session: AsyncSession, account_ids: list[str], requested: str | None
) -> str:
    """The requested currency, or the one most of the selected accounts are held in."""
    if requested:
        return requested.upper()
    return await AccountRepository(session).primary_currency(account_ids)


async def build_summary_aggregates(
    session: AsyncSession,
    *,
    user_id: str,
    account_id: str | None,
    period: str,
    currency: str | None = None,
    reference: datetime | None = None,
) -> SummaryResult:
    """Aggregates for one period in one currency, plus the change against the previous one."""
    reference = (reference or datetime.now(UTC)).astimezone(UTC)
    account_ids = await _account_ids(session, user_id, account_id)
    report_currency = await resolve_currency(session, account_ids, currency)
    transactions = TransactionRepository(session)

    start, end, label = period_bounds(period, reference)
    totals = await transactions.spend_by_category(
        account_ids=account_ids, date_from=start, date_to=end, currency=report_currency
    )
    rows = _to_rows(totals)
    total_spend = sum((row.amount for row in rows), Decimal("0"))
    other_currencies = [
        code
        for code in await transactions.spend_currencies(
            account_ids=account_ids, date_from=start, date_to=end
        )
        if code != report_currency
    ]

    prev_start, prev_end = previous_bounds(period, start)
    prev_totals = await transactions.spend_by_category(
        account_ids=account_ids,
        date_from=prev_start,
        date_to=prev_end,
        currency=report_currency,
    )
    prev_total = sum((Decimal(amount) for _slug, amount, _c in prev_totals), Decimal("0"))
    change_pct = (
        round(float((total_spend - prev_total) / prev_total) * 100, 1) if prev_total else None
    )

    aggregates = {
        "period": label,
        "currency": report_currency,
        "total_spend": float(total_spend),
        "by_category": [
            {"category": row.category, "amount": float(row.amount)} for row in rows[:8]
        ],
        "previous_period_total": float(prev_total),
        "change_vs_previous_pct": change_pct,
    }
    return SummaryResult(
        aggregates=aggregates,
        rows=rows,
        total_spend=total_spend,
        change_pct=change_pct,
        currency=report_currency,
        date_from=start,
        date_to=end,
        other_currencies=other_currencies,
    )


async def build_trends(
    session: AsyncSession,
    *,
    user_id: str,
    account_id: str | None,
    period: str,
    periods: int,
    currency: str | None = None,
    reference: datetime | None = None,
) -> tuple[list[TrendPoint], str]:
    """Structured time series for charting, in one currency. No LLM involved."""
    reference = reference or datetime.now(UTC)
    account_ids = await _account_ids(session, user_id, account_id)
    report_currency = await resolve_currency(session, account_ids, currency)
    transactions = TransactionRepository(session)

    points: list[TrendPoint] = []
    start, end, label = period_bounds(period, reference)
    for _ in range(periods):
        totals = await transactions.spend_by_category(
            account_ids=account_ids, date_from=start, date_to=end, currency=report_currency
        )
        rows = _to_rows(totals)
        points.append(
            TrendPoint(
                period=label,
                total=sum((row.amount for row in rows), Decimal("0")),
                by_category=rows,
            )
        )
        end = start
        start, _ = previous_bounds(period, start)
        label = f"{start:%Y-W%V}" if period == "weekly" else f"{start:%Y-%m}"

    return list(reversed(points)), report_currency
