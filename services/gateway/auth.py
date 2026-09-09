"""Edge authentication.

Deliberately *not* `libs.common.auth.current_user_id`: that one also accepts
`X-Internal-Token` + `X-User-Id`, which is right for a service sitting behind the
Gateway and wrong for the Gateway itself. At the public edge the only acceptable
credential is an end-user JWT, so a caller who somehow learned the internal token still
cannot use it to impersonate someone here.
"""

from __future__ import annotations

from fastapi import Header, Request

from libs.common.config import Settings, get_settings
from libs.common.errors import UnauthorizedError
from libs.common.security import decode_token


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


async def end_user_id(request: Request, authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("A bearer token is required.")
    payload = decode_token(authorization.split(" ", 1)[1], settings=_settings(request))
    subject = payload.get("sub")
    if not subject:
        raise UnauthorizedError("Token is missing a subject.")
    return str(subject)
