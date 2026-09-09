"""Insights endpoints: structured trends, and an AI summary over aggregates only."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.auth import current_user_id
from libs.db.session import get_session
from libs.llm_client.summarize import fallback_summary
from services.insights import service
from services.insights.schemas import SummaryResponse, TrendsResponse

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/summary", response_model=SummaryResponse, summary="AI spending summary")
async def get_summary(
    request: Request,
    account_id: str | None = Query(default=None),
    period: Literal["weekly", "monthly"] = Query(default="monthly"),
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> SummaryResponse:
    aggregates, rows, total_spend, change_pct = await service.build_summary_aggregates(
        session, user_id=user_id, account_id=account_id, period=period
    )

    summarizer = getattr(request.app.state, "summarizer", None)
    if summarizer is None:
        # No LLM configured: the numbers are still exact, the sentence is templated.
        summary = fallback_summary(
            aggregates["period"],
            float(total_spend),
            [(row.category, float(row.amount)) for row in rows],
        )
        source = "fallback"
    else:
        summary, source = await summarizer.summarize(aggregates)

    return SummaryResponse(
        period=aggregates["period"],
        total_spend=total_spend,
        by_category=rows,
        summary=summary,
        summary_source=source,
        change_vs_previous_pct=change_pct,
    )


@router.get("/trends", response_model=TrendsResponse, summary="Category spend over time")
async def get_trends(
    account_id: str | None = Query(default=None),
    period: Literal["weekly", "monthly"] = Query(default="monthly"),
    periods: int = Query(default=6, ge=1, le=24),
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> TrendsResponse:
    points = await service.build_trends(
        session, user_id=user_id, account_id=account_id, period=period, periods=periods
    )
    return TrendsResponse(periods=points)
