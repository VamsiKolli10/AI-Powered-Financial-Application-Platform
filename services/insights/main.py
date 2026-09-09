"""Insights service: aggregate reporting with an AI summary on top."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.app import create_app
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.redis_client import close_redis, get_redis
from libs.db.session import check_database, dispose_engine
from libs.llm_client.factory import build_summarizer
from services.insights.routes import router

settings = get_settings()
log = get_logger("insights")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis_client = get_redis(settings) if settings.llm_enabled else None
    app.state.summarizer = build_summarizer(settings, redis_client=redis_client)
    log.info("service_ready", summaries="llm" if app.state.summarizer else "template")
    yield
    await close_redis()
    await dispose_engine()


app = create_app(
    service_name="insights",
    title="Insights Service",
    description=(
        "Aggregate spending reporting. `/trends` is pure SQL; `/summary` adds a natural-"
        "language sentence generated from those aggregates - never from raw transactions."
    ),
    settings=settings,
    readiness_checks={"database": check_database},
    lifespan=lifespan,
)
app.state.summarizer = None
app.include_router(router)
