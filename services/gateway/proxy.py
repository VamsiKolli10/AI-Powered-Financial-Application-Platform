"""Reverse proxy to the downstream services.

Two things matter here:

1. **Identity is minted, never forwarded.** The end-user JWT is verified at the edge and
   replaced with `X-Internal-Token` + `X-User-Id`. Any client-supplied copy of those
   headers is stripped first, so a caller cannot present the internal headers itself and
   impersonate another user. Downstream services trust the internal token precisely
   because only the Gateway can produce it.
2. **The request id travels.** It is generated (or accepted) at the edge and forwarded, so
   one identifier ties the logs of every service together for a single request.
"""

from __future__ import annotations

from typing import Any

import httpx

from libs.common.auth import INTERNAL_TOKEN_HEADER, USER_ID_HEADER
from libs.common.config import Settings
from libs.common.errors import UpstreamError
from libs.common.logging import get_logger, request_id_ctx
from libs.common.middleware import REQUEST_ID_HEADER

log = get_logger("gateway.proxy")

# Never forwarded upstream: identity we mint ourselves, and hop-by-hop headers.
_STRIPPED_REQUEST_HEADERS = {
    INTERNAL_TOKEN_HEADER.lower(),
    USER_ID_HEADER.lower(),
    "authorization",
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
}
_STRIPPED_RESPONSE_HEADERS = {
    "content-length",
    "content-encoding",
    "connection",
    "keep-alive",
    "transfer-encoding",
}


class ServiceRouter:
    """Maps a public path prefix to a downstream base URL."""

    def __init__(self, settings: Settings) -> None:
        self.routes: dict[str, str] = {
            "transactions": settings.transactions_url,
            "assistant": settings.assistant_url,
            "insights": settings.insights_url,
            "notifications": settings.notifications_url,
        }

    def base_url(self, prefix: str) -> str | None:
        return self.routes.get(prefix)


def build_headers(incoming: Any, *, user_id: str, settings: Settings) -> dict[str, str]:
    headers = {
        key: value
        for key, value in incoming.items()
        if key.lower() not in _STRIPPED_REQUEST_HEADERS
    }
    headers[INTERNAL_TOKEN_HEADER] = settings.internal_service_token
    headers[USER_ID_HEADER] = user_id
    headers[REQUEST_ID_HEADER] = request_id_ctx.get() or ""
    return headers


def clean_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _STRIPPED_RESPONSE_HEADERS}


async def forward(
    client: httpx.AsyncClient,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    params: Any,
    content: bytes,
) -> httpx.Response:
    try:
        return await client.request(
            method, url, headers=headers, params=params, content=content or None
        )
    except httpx.TimeoutException as exc:
        log.warning("upstream_timeout", url=url)
        raise UpstreamError("The upstream service did not respond in time.") from exc
    except httpx.HTTPError as exc:
        log.warning("upstream_unreachable", url=url, error=str(exc))
        raise UpstreamError("The upstream service is unavailable.") from exc
