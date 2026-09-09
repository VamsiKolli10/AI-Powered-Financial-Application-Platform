"""Assistant service: conversational Q&A grounded in the user's own ledger."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.app import create_app
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.redis_client import check_redis, close_redis, get_redis
from libs.db.session import check_database, dispose_engine
from libs.llm_client.factory import build_responder
from services.assistant.conversations import (
    InMemoryConversationStore,
    RedisConversationStore,
)
from services.assistant.routes import router

settings = get_settings()
log = get_logger("assistant")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis_client = get_redis(settings) if settings.llm_enabled else None
    app.state.responder = build_responder(settings, redis_client=redis_client)
    app.state.conversations = (
        RedisConversationStore(redis_client)
        if redis_client is not None
        else InMemoryConversationStore()
    )
    log.info(
        "service_ready",
        replies="llm" if app.state.responder else "deterministic",
        history="redis" if redis_client is not None else "memory",
    )
    yield
    await close_redis()
    await dispose_engine()


app = create_app(
    service_name="assistant",
    title="Assistant Service",
    description=(
        "Answers questions about the user's own transactions. Figures are computed from "
        "PostgreSQL and the model only rewords them; the assistant is read-only and refuses "
        "anything that implies a write."
    ),
    settings=settings,
    readiness_checks=(
        {"database": check_database, "redis": check_redis}
        if settings.llm_enabled
        else {"database": check_database}
    ),
    lifespan=lifespan,
)
app.state.responder = None
app.state.conversations = None
app.include_router(router)
