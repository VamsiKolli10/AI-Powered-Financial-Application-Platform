"""Shared FastAPI application factory: logging, errors, metrics, health probes."""

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from libs.common.config import Settings, get_settings
from libs.common.errors import register_exception_handlers
from libs.common.logging import configure_logging, get_logger
from libs.common.middleware import RequestContextMiddleware

ReadinessCheck = Callable[[], Awaitable[None]]


def create_app(
    *,
    service_name: str,
    title: str,
    description: str = "",
    settings: Settings | None = None,
    readiness_checks: dict[str, ReadinessCheck] | None = None,
    lifespan: Callable[[FastAPI], Any] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(service_name, settings.log_level)
    log = get_logger(service_name)
    checks = readiness_checks or {}

    @asynccontextmanager
    async def _default_lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        log.info("service_starting", environment=settings.environment)
        yield
        log.info("service_stopping")

    app = FastAPI(
        title=title,
        description=description,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan or _default_lifespan,
    )
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware, service_name=service_name)
    register_exception_handlers(app)

    ops = APIRouter(tags=["ops"])

    @ops.get("/health", summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": service_name}

    @ops.get("/ready", summary="Readiness probe")
    async def ready() -> Response:
        results: dict[str, str] = {}
        healthy = True
        for name, check in checks.items():
            try:
                await check()
                results[name] = "ok"
            except Exception as exc:  # noqa: BLE001 - readiness must never raise
                healthy = False
                results[name] = f"error: {exc}"
                log.warning("readiness_check_failed", dependency=name, error=str(exc))
        body = {"status": "ready" if healthy else "not_ready", "checks": results}
        return Response(
            content=__import__("json").dumps(body),
            media_type="application/json",
            status_code=200 if healthy else 503,
        )

    @ops.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(ops)
    return app
