"""Persist enrichment jobs and events before attempting external work.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _work_columns() -> list[sa.Column]:
    return [
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("lock_token", sa.String(40)),
        sa.Column("last_error", sa.String(80)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "categorization_jobs",
        sa.Column(
            "id",
            sa.String(40),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        *_work_columns(),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "transaction_id",
            sa.String(40),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("partition_key", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        *_work_columns(),
        sa.UniqueConstraint("transaction_id", "event_type", name="uq_outbox_transaction_type"),
    )
    for table, name in (
        ("categorization_jobs", "ix_categorization_jobs_pending"),
        ("outbox_events", "ix_outbox_pending"),
    ):
        op.create_index(
            name,
            table,
            ["available_at", "created_at"],
            postgresql_where=sa.text("completed_at IS NULL"),
            sqlite_where=sa.text("completed_at IS NULL"),
        )
    op.create_index("ix_outbox_transaction", "outbox_events", ["transaction_id"])
    op.create_index("ix_outbox_partition", "outbox_events", ["partition_key", "created_at", "id"])
    # Recover records stranded by the former in-process background tasks. Historical
    # categorized rows are deliberately not replayed into new notifications.
    op.execute(
        "INSERT INTO categorization_jobs (id) "
        "SELECT id FROM transactions WHERE status = 'PENDING_CATEGORIZATION'"
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("categorization_jobs")
