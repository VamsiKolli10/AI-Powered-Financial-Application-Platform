"""Track pending delivery and scope legacy budget keys to user and currency.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("delivered_at", sa.DateTime(timezone=True)))
    # Historical alerts predate delivery tracking. Do not resend them on replay.
    op.execute("UPDATE notifications SET delivered_at = created_at")
    notifications = sa.table(
        "notifications",
        sa.column("id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("transaction_id", sa.String),
        sa.column("dedupe_key", sa.String),
    )
    transactions = sa.table(
        "transactions", sa.column("id", sa.String), sa.column("currency", sa.String)
    )
    key = notifications.c.dedupe_key
    length = sa.func.length(key)
    currency = sa.func.coalesce(
        sa.select(transactions.c.currency)
        .where(transactions.c.id == notifications.c.transaction_id)
        .correlate(notifications)
        .scalar_subquery(),
        "USD",
    )
    # Legacy keys contain exactly two separators: budget:<category>:YYYY-MM.
    # Use portable SQL so PostgreSQL offline migration rendering needs no live rows.
    category = sa.func.substr(key, 8, length - 15)
    month = sa.func.substr(key, length - 6, 7)
    op.execute(
        notifications.update()
        .where(key.like("budget:%"), length - sa.func.length(sa.func.replace(key, ":", "")) == 2)
        .values(
            dedupe_key=sa.literal("budget:")
            + notifications.c.user_id
            + ":"
            + currency
            + ":"
            + category
            + ":"
            + month
        )
    )


def downgrade() -> None:
    # Keep scoped keys: collapsing them would collide across users/currencies.
    with op.batch_alter_table("notifications") as batch:
        batch.drop_column("delivered_at")
