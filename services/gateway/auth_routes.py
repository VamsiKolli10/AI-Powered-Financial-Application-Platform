"""Authentication. The only place an end-user credential is ever handled."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import Settings, get_settings
from libs.common.errors import ConflictError, UnauthorizedError
from libs.common.logging import get_logger
from libs.common.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from libs.db.repositories import AccountRepository, UserRepository
from libs.db.session import get_session
from services.gateway.auth import end_user_id
from services.gateway.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

log = get_logger("gateway.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _tokens(user_id: str, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(subject=user_id, settings=settings),
        refresh_token=create_refresh_token(subject=user_id, settings=settings),
        expires_in=settings.jwt_expire_minutes * 60,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    settings = _settings(request)
    users = UserRepository(session)
    if await users.get_by_email(payload.email) is not None:
        raise ConflictError("An account with that email already exists.")

    user = await users.create(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
    )
    # A user with no account cannot ingest anything, so give them one to start with.
    await AccountRepository(session).create(user_id=user.id, name="Everyday Checking")
    log.info("user_registered", user_id=user.id)
    return _tokens(user.id, settings)


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials for a JWT")
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    settings = _settings(request)
    user = await UserRepository(session).get_by_email(payload.email)

    # One message for both cases: never reveal whether an email is registered.
    if user is None or not verify_password(payload.password, user.hashed_password):
        log.info("login_failed")
        raise UnauthorizedError("Incorrect email or password.")
    if not user.is_active:
        raise UnauthorizedError("This account is disabled.")

    log.info("login_succeeded", user_id=user.id)
    return _tokens(user.id, settings)


@router.post("/refresh", response_model=TokenResponse, summary="Refresh an expiring token")
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    settings = _settings(request)
    claims = decode_token(payload.refresh_token, settings=settings, expected_type="refresh")
    user = await UserRepository(session).get(str(claims.get("sub", "")))
    if user is None or not user.is_active:
        raise UnauthorizedError("This token no longer corresponds to an active account.")
    return _tokens(user.id, settings)


@router.get("/me", response_model=UserResponse, summary="The authenticated user")
async def me(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(end_user_id),
) -> UserResponse:
    user = await UserRepository(session).get(user_id)
    if user is None:
        raise UnauthorizedError("This token no longer corresponds to an account.")
    return UserResponse(id=user.id, email=user.email, full_name=user.full_name)
