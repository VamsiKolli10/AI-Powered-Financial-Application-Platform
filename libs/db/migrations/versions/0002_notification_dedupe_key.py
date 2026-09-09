"""Add notifications.dedupe_key for idempotent alert creation.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows (there are none in practice) get their id as a unique placeholder.
    op.add_column("notifications", sa.Column("dedupe_key", sa.String(160), nullable=True))
    op.execute("UPDATE notifications SET dedupe_key = id WHERE dedupe_key IS NULL")
    with op.batch_alter_table("notifications") as batch:
        batch.alter_column("dedupe_key", existing_type=sa.String(160), nullable=False)
        batch.create_unique_constraint("uq_notifications_dedupe_key", ["dedupe_key"])


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("uq_notifications_dedupe_key", type_="unique")
        batch.drop_column("dedupe_key")
