"""Insights endpoints: structured trends, and an AI summary over aggregates only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.auth import current_user_id
from libs.common.errors import ValidationError
from libs.db.session import get_session
from libs.llm_client.summarize import fallback_summary
from services.insights import service
from services.insights.schemas import SummaryResponse, TrendsResponse

router = APIRouter(prefix="/insights", tags=["insights"])

CurrencyQuery = Query(
    default=None,
    min_length=3,
    max_length=3,
    pattern="^[A-Za-z]{3}$",
    description="ISO 4217 code. Defaults to the currency most of your accounts are held in.",
)


@router.get("/summary", response_model=SummaryResponse, summary="AI spending summary")
async def get_summary(
    request: Request,
    account_id: str | None = Query(default=None),
    period: Literal["weekly", "monthly"] = Query(default="monthly"),
    currency: str | None = CurrencyQuery,
    as_of: datetime | None = Query(default=None, description="Reporting end time, with timezone."),
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> SummaryResponse:
    if as_of is not None and (as_of.tzinfo is None or as_of > datetime.now(UTC)):
        raise ValidationError("as_of must include a timezone and must not be in the future.")
    result = await service.build_summary_aggregates(
        session,
        user_id=user_id,
        account_id=account_id,
        period=period,
        currency=currency,
        reference=as_of,
    )
    aggregates = result.aggregates

    summarizer = getattr(request.app.state, "summarizer", None)
    if summarizer is None:
        # No LLM configured: the numbers are still exact, the sentence is templated.
        summary = fallback_summary(
            aggregates["period"],
            float(result.total_spend),
            [(row.category, float(row.amount)) for row in result.rows],
            currency=result.currency,
        )
        source = "fallback"
    else:
        summary, source = await summarizer.summarize(aggregates)

    return SummaryResponse(
        period=aggregates["period"],
        date_from=result.date_from,
        date_to=result.date_to,
        currency=result.currency,
        total_spend=result.total_spend,
        by_category=result.rows,
        summary=summary,
        summary_source=source,
        change_vs_previous_pct=result.change_pct,
        other_currencies=result.other_currencies,
    )


@router.get("/trends", response_model=TrendsResponse, summary="Category spend over time")
async def get_trends(
    account_id: str | None = Query(default=None),
    period: Literal["weekly", "monthly"] = Query(default="monthly"),
    periods: int = Query(default=6, ge=1, le=24),
    currency: str | None = CurrencyQuery,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> TrendsResponse:
    points, report_currency = await service.build_trends(
        session,
        user_id=user_id,
        account_id=account_id,
        period=period,
        periods=periods,
        currency=currency,
    )
    return TrendsResponse(currency=report_currency, periods=points)
