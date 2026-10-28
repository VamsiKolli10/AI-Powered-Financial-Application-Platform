"""Platform status for signed-in clients.

The dashboard's status card needs to know whether each service can serve traffic, which
is exactly what every service's `/ready` probe answers. Those probes live on the private
network, so the Gateway asks them on the client's behalf and reports one word per
service. Error details stay in the logs: `/ready` bodies name internal hosts and
exception text, which a browser has no business seeing.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import Settings, get_settings
from libs.common.logging import get_logger
from libs.db.session import get_session
from services.gateway.auth import end_user_id
from services.gateway.proxy import ServiceRouter

log = get_logger("gateway.status")
router = APIRouter(tags=["status"])

# Probes are cheap, but a page of open dashboards polling every service is not; a short
# shared cache keeps the fan-out to one round per window per Gateway replica.
CACHE_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 2.0

State = Literal["ok", "unavailable"]


class PlatformStatus(BaseModel):
    status: Literal["ok", "degraded"]
    services: dict[str, State]
    checked_at: datetime


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


async def _probe(client: httpx.AsyncClient, name: str, url: str) -> State:
    try:
        response = await client.get(url, timeout=PROBE_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        log.warning("status_probe_failed", service=name, error=str(exc))
        return "unavailable"
    if response.status_code != 200:
        log.warning("status_probe_not_ready", service=name, status=response.status_code)
        return "unavailable"
    return "ok"


async def _gateway_state(session: AsyncSession) -> State:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - a status check must answer, not raise
        log.warning("status_probe_failed", service="gateway", error=str(exc))
        return "unavailable"
    return "ok"


@router.get("/status", response_model=PlatformStatus, summary="Readiness of every service")
async def platform_status(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(end_user_id),
) -> PlatformStatus:
    await request.app.state.rate_limiter.check(user_id=user_id, path="/status")

    cached: tuple[float, PlatformStatus] | None = getattr(request.app.state, "status_cache", None)
    if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    routes = ServiceRouter(_settings(request)).routes
    client: httpx.AsyncClient = request.app.state.http_client
    gateway, *downstream = await asyncio.gather(
        _gateway_state(session),
        *(_probe(client, name, f"{base_url}/ready") for name, base_url in routes.items()),
    )
    services: dict[str, State] = {"gateway": gateway, **dict(zip(routes, downstream, strict=True))}
    result = PlatformStatus(
        status="ok" if all(state == "ok" for state in services.values()) else "degraded",
        services=services,
        checked_at=datetime.now(UTC),
    )
    request.app.state.status_cache = (time.monotonic(), result)
    return result
