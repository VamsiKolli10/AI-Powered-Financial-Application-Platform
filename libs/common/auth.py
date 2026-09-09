"""Downstream-service authentication.

The Gateway verifies the end-user JWT and forwards identity to internal services as
`X-Internal-Token` + `X-User-Id` (ARCHITECTURE.md sec. 7). Calling a service directly
with a user Bearer token also works, which keeps local development and tests simple.
"""

from fastapi import Depends, Header, Request

from libs.common.config import Settings, get_settings
from libs.common.errors import UnauthorizedError
from libs.common.security import decode_token

INTERNAL_TOKEN_HEADER = "X-Internal-Token"
USER_ID_HEADER = "X-User-Id"


def _settings_from(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


async def current_user_id(
    request: Request,
    x_internal_token: str | None = Header(default=None, alias=INTERNAL_TOKEN_HEADER),
    x_user_id: str | None = Header(default=None, alias=USER_ID_HEADER),
    authorization: str | None = Header(default=None),
) -> str:
    settings = _settings_from(request)

    if x_internal_token:
        if x_internal_token != settings.internal_service_token:
            raise UnauthorizedError("Invalid internal service token.")
        if not x_user_id:
            raise UnauthorizedError(f"{USER_ID_HEADER} is required with an internal token.")
        return x_user_id

    if authorization and authorization.lower().startswith("bearer "):
        payload = decode_token(authorization.split(" ", 1)[1], settings=settings)
        subject = payload.get("sub")
        if not subject:
            raise UnauthorizedError("Token is missing a subject.")
        return str(subject)

    raise UnauthorizedError("Authentication required.")


CurrentUser = Depends(current_user_id)
