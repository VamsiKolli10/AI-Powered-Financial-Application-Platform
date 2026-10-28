"""Exercise upgrade with historical alerts rather than only creating ORM tables."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_upgrade_preserves_old_alerts_and_scopes_budget_keys(tmp_path):
    database = tmp_path / "migration.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    root = Path(__file__).resolve().parents[2]

    def migrate(revision):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    migrate("0002")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO users (id,email,hashed_password,is_active) VALUES (?,?,?,?)",
            ("usr_legacy", "legacy@example.test", "x", True),
        )
        connection.execute(
            "INSERT INTO accounts (id,user_id,name,currency,balance) VALUES (?,?,?,?,?)",
            ("acct_legacy", "usr_legacy", "Checking", "EUR", 0),
        )
        connection.execute(
            "INSERT INTO transactions "
            "(id,account_id,external_tx_id,amount,currency,description,normalized_description,"
            "status,occurred_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "tx_legacy",
                "acct_legacy",
                "legacy",
                -1600,
                "EUR",
                "Grocer",
                "grocer",
                "CATEGORIZED",
                "2026-10-01 12:00:00",
            ),
        )
        connection.execute(
            "INSERT INTO notifications "
            "(id,user_id,transaction_id,type,message,dedupe_key,read) VALUES (?,?,?,?,?,?,?)",
            (
                "notif_legacy",
                "usr_legacy",
                "tx_legacy",
                "BUDGET_THRESHOLD",
                "Groceries",
                "budget:groceries:2026-10",
                False,
            ),
        )
    migrate("head")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        row = connection.execute(
            "SELECT dedupe_key,delivered_at,created_at FROM notifications WHERE id=?",
            ("notif_legacy",),
        ).fetchone()
        assert row[0] == "budget:usr_legacy:EUR:groceries:2026-10"
        assert row[1] == row[2] and row[1] is not None
        connection.execute(
            "INSERT INTO notifications "
            "(id,user_id,type,message,dedupe_key,read) VALUES (?,?,?,?,?,?)",
            ("notif_new", "usr_legacy", "LARGE_TRANSACTION", "New", "large:tx_new", False),
        )
        assert (
            connection.execute(
                "SELECT delivered_at FROM notifications WHERE id='notif_new'"
            ).fetchone()[0]
            is None
        )


def test_postgres_migrations_render_without_a_live_database():
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://audit:audit@localhost/audit",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "ADD COLUMN delivered_at" in result.stdout
    assert "notifications.user_id" in result.stdout and "coalesce" in result.stdout
