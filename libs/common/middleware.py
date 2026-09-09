"""Request-ID propagation and Prometheus request metrics."""

import time
import uuid
from collections.abc import Awaitable, Callable

from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from libs.common.logging import get_logger, request_id_ctx

log = get_logger(__name__)

REQUEST_COUNT = Counter(
    "http_requests_total", "HTTP requests", ["service", "method", "path", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["service", "method", "path"]
)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates a request id and records structured access logs + metrics."""

    def __init__(self, app: Callable, service_name: str) -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        token = request_id_ctx.set(rid)
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            REQUEST_COUNT.labels(self.service_name, request.method, path, "500").inc()
            request_id_ctx.reset(token)
            raise
        elapsed = time.perf_counter() - started
        # Re-read the matched route: it is only resolved once routing has run.
        route = request.scope.get("route")
        path = getattr(route, "path", path)
        REQUEST_COUNT.labels(self.service_name, request.method, path, str(status)).inc()
        REQUEST_LATENCY.labels(self.service_name, request.method, path).observe(elapsed)
        response.headers[REQUEST_ID_HEADER] = rid
        if path not in ("/health", "/ready", "/metrics"):
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=status,
                duration_ms=round(elapsed * 1000, 2),
            )
        request_id_ctx.reset(token)
        return response
