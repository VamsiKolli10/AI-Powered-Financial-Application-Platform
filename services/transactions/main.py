"""Transactions service entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.app import create_app
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.redis_client import check_redis, close_redis, get_redis
from libs.db.session import check_database, dispose_engine, session_scope
from libs.events import build_publisher
from libs.llm_client.factory import build_categorizer
from services.transactions.routes import router

settings = get_settings()
log = get_logger("transactions")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis_client = get_redis(settings) if settings.llm_enabled else None
    # None here means "rules engine only" - a valid, fully working mode.
    app.state.classifier = build_categorizer(settings, redis_client=redis_client)
    app.state.session_scope = session_scope
    publisher = build_publisher(settings, client_id="transactions")
    await publisher.start()
    app.state.publisher = publisher
    log.info(
        "service_ready",
        llm_enabled=settings.llm_enabled,
        classifier="llm" if app.state.classifier else "rules",
        events="kafka" if settings.kafka_enabled else "disabled",
    )
    yield
    await publisher.stop()
    await close_redis()
    await dispose_engine()


app = create_app(
    service_name="transactions",
    title="Transactions Service",
    description=(
        "Ingests transactions, writes them durably, and enriches them with a category and "
        "anomaly score asynchronously. The only service that writes financial records."
    ),
    settings=settings,
    readiness_checks=(
        {"database": check_database, "redis": check_redis}
        if settings.llm_enabled
        else {"database": check_database}
    ),
    lifespan=lifespan,
)
app.state.session_scope = session_scope
app.state.classifier = None
app.state.publisher = None
app.include_router(router)
