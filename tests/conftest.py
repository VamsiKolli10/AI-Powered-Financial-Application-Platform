"""Test fixtures.

Unit and service tests run against an in-memory SQLite database so the suite needs no
Docker. Tests marked `integration` run against whatever DATABASE_URL points at
(Postgres in CI) and are skipped when that is not configured.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("KAFKA_ENABLED", "false")
os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "test-internal-token")
os.environ.setdefault("JWT_SECRET", "test-secret")

from libs.common.config import get_settings  # noqa: E402
from libs.db.categories import CATEGORIES  # noqa: E402
from libs.db.models import Account, Base, Category, User  # noqa: E402
from libs.db.session import get_session  # noqa: E402

TEST_USER_ID = "usr_test"
OTHER_USER_ID = "usr_other"


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessionmaker_(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def session(sessionmaker_) -> AsyncIterator[AsyncSession]:
    async with sessionmaker_() as session:
        for slug, name, description in CATEGORIES:
            session.add(Category(slug=slug, name=name, description=description))
        session.add(
            User(id=TEST_USER_ID, email="test@example.com", hashed_password="x", full_name="Test")
        )
        session.add(
            User(
                id=OTHER_USER_ID, email="other@example.com", hashed_password="x", full_name="Other"
            )
        )
        session.add(Account(id="acct_test", user_id=TEST_USER_ID, name="Checking", currency="USD"))
        session.add(Account(id="acct_other", user_id=OTHER_USER_ID, name="Theirs", currency="USD"))
        await session.commit()
        yield session


@pytest.fixture
def app():
    """The Transactions FastAPI app, reset between tests."""
    from services.transactions.main import app as transactions_app

    transactions_app.state.classifier = None
    yield transactions_app
    transactions_app.dependency_overrides.clear()
    transactions_app.state.classifier = None


@pytest.fixture
async def client(app, sessionmaker_, session) -> AsyncIterator[AsyncClient]:
    """Transactions service client with the DB dependency pointed at SQLite."""

    async def _override() -> AsyncIterator[AsyncSession]:
        async with sessionmaker_() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    @asynccontextmanager
    async def _scope() -> AsyncIterator[AsyncSession]:
        async with sessionmaker_() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    app.state.session_scope = _scope
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={
            "X-Internal-Token": get_settings().internal_service_token,
            "X-User-Id": TEST_USER_ID,
        },
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def make_client(sessionmaker_, session):
    """Build a test client for any of the services, sharing the test database."""
    from contextlib import asynccontextmanager

    opened: list[tuple[AsyncClient, object]] = []

    async def _factory(target_app) -> AsyncClient:
        async def _override() -> AsyncIterator[AsyncSession]:
            async with sessionmaker_() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        @asynccontextmanager
        async def _scope() -> AsyncIterator[AsyncSession]:
            async with sessionmaker_() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        target_app.dependency_overrides[get_session] = _override
        target_app.state.session_scope = _scope
        client = AsyncClient(
            transport=ASGITransport(app=target_app),
            base_url="http://test",
            headers={
                "X-Internal-Token": get_settings().internal_service_token,
                "X-User-Id": TEST_USER_ID,
            },
        )
        opened.append((client, target_app))
        return client

    yield _factory

    for client, target_app in opened:
        await client.aclose()
        target_app.dependency_overrides.clear()


@pytest.fixture
def session_scope_factory(sessionmaker_):
    """A session_scope bound to the test database, for handlers under test."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope() -> AsyncIterator[AsyncSession]:
        async with sessionmaker_() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    return _scope


@pytest.fixture
def tx_payload() -> dict:
    return {
        "account_id": "acct_test",
        "external_tx_id": "bank_tx_98765",
        "amount": -42.50,
        "currency": "USD",
        "description": "STARBUCKS #1234 SEATTLE WA",
        "occurred_at": datetime(2026, 9, 5, 14, 30, tzinfo=UTC).isoformat(),
    }
