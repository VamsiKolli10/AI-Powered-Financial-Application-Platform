"""API Gateway: the single entry point. Auth, routing, rate limiting."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI

from libs.common.app import create_app
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.redis_client import close_redis, get_redis
from libs.db.session import check_database, dispose_engine
from services.gateway.auth_routes import router as auth_router
from services.gateway.rate_limit import UserRateLimiter
from services.gateway.routes import router as proxy_router

settings = get_settings()
log = get_logger("gateway")

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0))
    redis_client = get_redis(settings)
    app.state.rate_limiter = UserRateLimiter(settings, redis_client=redis_client)
    log.info("service_ready", api_prefix=API_PREFIX)
    yield
    await app.state.http_client.aclose()
    await close_redis()
    await dispose_engine()


app = create_app(
    service_name="gateway",
    title="API Gateway",
    description=(
        "Single entry point for clients. Verifies the end-user JWT, applies per-user rate "
        "limits, and forwards to the downstream services with an internal identity header."
    ),
    settings=settings,
    readiness_checks={"database": check_database},
    lifespan=lifespan,
)

api = APIRouter(prefix=API_PREFIX)
api.include_router(auth_router)
api.include_router(proxy_router)
app.include_router(api)

# Set on the app so tests can substitute both without touching the lifespan.
app.state.http_client = None
app.state.rate_limiter = UserRateLimiter(settings)
