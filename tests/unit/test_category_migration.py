"""Migrations alone must produce a usable taxonomy without demo credentials."""

import os
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from libs.db.categories import CATEGORIES
from libs.db.models import Account, CategorizationJob, OutboxEvent, Transaction, User
from services.transactions.schemas import TransactionCreate
from services.transactions.service import ingest_transaction
from services.transactions.workers import TransactionWorkers


def migrate(database, revision, direction="upgrade"):
    subprocess.run(
        [sys.executable, "-m", "alembic", direction, revision],
        cwd=Path(__file__).resolve().parents[2],
        env={
            "PATH": os.defpath,
            "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
            "ENVIRONMENT": "test",
            "KAFKA_ENABLED": "false",
            "LLM_ENABLED": "false",
            "OPENAI_API_KEY": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=True,
        capture_output=True,
    )


async def test_fresh_migrations_allow_categorization_without_demo_seed(tmp_path):
    database = tmp_path / "fresh.db"
    migrate(database, "head")
    with sqlite3.connect(database) as connection:
        assert set(connection.execute("SELECT slug,name,description FROM categories")) == set(
            CATEGORIES
        )
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0

    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def scope():
        async with sessions.begin() as session:
            yield session

    try:
        async with scope() as session:
            session.add(User(id="u", email="fresh@example.com", hashed_password="test"))
            await session.flush()
            session.add(Account(id="a", user_id="u", name="Checking", currency="USD"))
        async with scope() as session:
            result = await ingest_transaction(
                session,
                user_id="u",
                payload=TransactionCreate(
                    account_id="a",
                    external_tx_id="fresh-entry",
                    description="STARBUCKS",
                    amount=Decimal("-12.34"),
                    occurred_at=datetime.now(UTC),
                ),
            )
            transaction_id = result.transaction.id
        assert await TransactionWorkers(scope).categorize_once()
        async with scope() as session:
            transaction = await session.get(Transaction, transaction_id)
            job = await session.get(CategorizationJob, transaction_id)
            assert transaction.status.value == "categorized"
            assert transaction.category_slug == "dining_coffee"
            assert job.completed_at is not None
            assert await session.scalar(select(func.count()).select_from(OutboxEvent)) >= 1
    finally:
        await engine.dispose()


def test_taxonomy_upgrade_preserves_existing_rows_and_can_be_reapplied(tmp_path):
    database = tmp_path / "existing.db"
    migrate(database, "0004")
    existing = ("groceries", "Existing name", "Existing metadata")
    custom = ("custom", "Custom category", "User metadata")
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO categories(slug,name,description) VALUES (?,?,?)", [existing, custom]
        )
    migrate(database, "head")
    migrate(database, "0004", "downgrade")
    migrate(database, "head")
    migrate(database, "head")
    with sqlite3.connect(database) as connection:
        rows = set(connection.execute("SELECT slug,name,description FROM categories"))
        assert rows == {row for row in CATEGORIES if row[0] != "groceries"} | {existing, custom}
