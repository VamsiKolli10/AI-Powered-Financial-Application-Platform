"""Upgrade recovers stranded jobs without replaying historical categorized rows."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_durable_job_migration_only_backfills_pending(tmp_path):
    database = tmp_path / "jobs.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}

    def migrate(revision):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            check=True,
            capture_output=True,
        )

    migrate("0003")
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO users (id,email,hashed_password,is_active) "
            "VALUES ('u','migration@example.com','x',1)"
        )
        db.execute(
            "INSERT INTO accounts (id,user_id,name,currency,balance) "
            "VALUES ('a','u','Checking','USD',0)"
        )
        for tx_status in ("PENDING_CATEGORIZATION", "CATEGORIZED"):
            db.execute(
                "INSERT INTO transactions "
                "(id,account_id,external_tx_id,amount,currency,description,"
                "normalized_description,status,occurred_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    tx_status,
                    "a",
                    tx_status,
                    -10,
                    "USD",
                    "Legacy",
                    "legacy",
                    tx_status,
                    "2026-10-01",
                ),
            )
    migrate("head")
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT id,attempts,completed_at FROM categorization_jobs").fetchall()
        assert rows == [("PENDING_CATEGORIZATION", 0, None)]
        assert db.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 0
