"""Regression test for the ingest -> categorize handoff.

Uses a file-backed SQLite database on purpose: an in-memory one shares a single
connection, so an uncommitted row would still be visible to the background task and
the race this test guards against would go unnoticed.
"""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from libs.common.config import get_settings
from libs.db.categories import CATEGORIES
from libs.db.models import Account, Base, Category, User
from libs.db.session import get_session
from tests.conftest import TEST_USER_ID


@pytest.fixture
async def file_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with maker() as session:
        for slug, name, description in CATEGORIES:
            session.add(Category(slug=slug, name=name, description=description))
        session.add(User(id=TEST_USER_ID, email="t@example.com", hashed_password="x"))
        session.add(Account(id="acct_test", user_id=TEST_USER_ID, name="Checking"))
        await session.commit()
    yield maker
    await engine.dispose()


async def test_background_task_sees_the_committed_row(file_db):
    from contextlib import asynccontextmanager

    from services.transactions.main import app

    async def _override():
        async with file_db() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    @asynccontextmanager
    async def _scope():
        async with file_db() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    app.state.session_scope = _scope
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={
                "X-Internal-Token": get_settings().internal_service_token,
                "X-User-Id": TEST_USER_ID,
            },
        ) as client:
            created = await client.post(
                "/transactions",
                json={
                    "account_id": "acct_test",
                    "external_tx_id": "bg_1",
                    "amount": -42.50,
                    "currency": "USD",
                    "description": "STARBUCKS #1234 SEATTLE WA",
                    "occurred_at": datetime(2026, 9, 5, 14, 30, tzinfo=UTC).isoformat(),
                },
            )
            assert created.status_code == 202
            fetched = await client.get(f"/transactions/{created.json()['id']}")

        assert fetched.json()["status"] == "categorized"
        assert fetched.json()["category"] == "Dining & Coffee"
    finally:
        app.dependency_overrides.clear()
        app.state.session_scope = None
