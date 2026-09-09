"""Public API surface: everything a client calls, under /api/v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from libs.common.config import Settings, get_settings
from libs.common.errors import NotFoundError
from libs.common.logging import get_logger
from services.gateway.auth import end_user_id
from services.gateway.proxy import (
    ServiceRouter,
    build_headers,
    clean_response_headers,
    forward,
)

log = get_logger("gateway")
router = APIRouter()

PROXIED = ("transactions", "assistant", "insights", "notifications")


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE"]


# Two routes rather than one optional path: "/transactions" and "/transactions/{id}"
# must both proxy, and a single "{path:path}" pattern does not match the bare prefix
# (Starlette answers it with a 307 to the trailing-slash form instead).
@router.api_route("/{service}", methods=METHODS, include_in_schema=False)
@router.api_route("/{service}/{path:path}", methods=METHODS, include_in_schema=False)
async def proxy(
    service: str,
    request: Request,
    path: str = "",
    user_id: str = Depends(end_user_id),
) -> Response:
    if service not in PROXIED:
        raise NotFoundError(f"Unknown service '{service}'.")

    settings = _settings(request)
    limiter = request.app.state.rate_limiter
    await limiter.check(user_id=user_id, path=f"/{service}/{path}")

    base_url = ServiceRouter(settings).base_url(service)
    target = f"{base_url}/{service}/{path}".rstrip("/")

    response = await forward(
        request.app.state.http_client,
        method=request.method,
        url=target,
        headers=build_headers(request.headers, user_id=user_id, settings=settings),
        params=request.query_params,
        content=await request.body(),
    )
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=clean_response_headers(response.headers),
        media_type=response.headers.get("content-type"),
    )
