"""Initial schema: users, accounts, categories, transactions, notifications, audit_log.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "accounts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("institution", sa.String(120)),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("balance", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_accounts_user_id", "accounts", ["user_id"])

    op.create_table(
        "categories",
        sa.Column("slug", sa.String(60), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(40),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_tx_id", sa.String(120), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("normalized_description", sa.String(500), nullable=False),
        sa.Column("merchant", sa.String(200)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "category_slug",
            sa.String(60),
            sa.ForeignKey("categories.slug", ondelete="SET NULL"),
        ),
        sa.Column("category_source", sa.String(16)),
        sa.Column("anomaly_score", sa.Float()),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("categorized_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "account_id", "external_tx_id", name="uq_transactions_account_external"
        ),
    )
    op.create_index(
        "ix_transactions_account_occurred", "transactions", ["account_id", "occurred_at"]
    )
    op.create_index("ix_transactions_status", "transactions", ["status"])
    op.create_index(
        "ix_transactions_normalized_description", "transactions", ["normalized_description"]
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id",
            sa.String(40),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_entity", "audit_log", ["entity_type", "entity_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("notifications")
    op.drop_table("transactions")
    op.drop_table("categories")
    op.drop_table("accounts")
    op.drop_table("users")
