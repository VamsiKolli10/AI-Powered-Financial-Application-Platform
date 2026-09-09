"""SQLAlchemy models: the system of record (see ARCHITECTURE.md sec. 3)."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from libs.db.ids import new_id


class Base(DeclarativeBase):
    pass


class TransactionStatus(enum.StrEnum):
    PENDING_CATEGORIZATION = "pending_categorization"
    CATEGORIZED = "categorized"
    CATEGORIZATION_FAILED = "categorization_failed"


class CategorySource(enum.StrEnum):
    RULES = "rules"
    LLM = "llm"
    USER = "user"


class NotificationType(enum.StrEnum):
    LARGE_TRANSACTION = "large_transaction"
    ANOMALY_FLAGGED = "anomaly_flagged"
    BUDGET_THRESHOLD = "budget_threshold"


_ts = DateTime(timezone=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        _ts, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("usr"))
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    accounts: Mapped[list[Account]] = relationship(back_populates="user", cascade="all, delete")


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("acct"))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    institution: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    # Maintained deterministically by the Transactions service - never by an LLM.
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)

    user: Mapped[User] = relationship(back_populates="accounts")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="account", cascade="all, delete"
    )


class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    slug: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        # Idempotency for retried bank-feed deliveries (ARCHITECTURE.md sec. 6).
        UniqueConstraint("account_id", "external_tx_id", name="uq_transactions_account_external"),
        Index("ix_transactions_account_occurred", "account_id", "occurred_at"),
        Index("ix_transactions_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("tx"))
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    external_tx_id: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    # Redacted/normalized form - the only description ever sent to the LLM provider.
    normalized_description: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    merchant: Mapped[str | None] = mapped_column(String(200))

    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, native_enum=False, length=32),
        default=TransactionStatus.PENDING_CATEGORIZATION,
        nullable=False,
    )
    category_slug: Mapped[str | None] = mapped_column(
        ForeignKey("categories.slug", ondelete="SET NULL")
    )
    category_source: Mapped[CategorySource | None] = mapped_column(
        Enum(CategorySource, native_enum=False, length=16)
    )
    anomaly_score: Mapped[float | None] = mapped_column(Float)

    occurred_at: Mapped[datetime] = mapped_column(_ts, nullable=False)
    categorized_at: Mapped[datetime | None] = mapped_column(_ts)

    account: Mapped[Account] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship()


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        # At-least-once delivery means the same event can arrive twice; this makes
        # inserting the resulting alert idempotent.
        UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("notif"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE")
    )
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, native_enum=False, length=32), nullable=False
    )
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AuditLog(Base):
    """Append-only trail for every money-affecting or AI-assisted write."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("audit"))
    actor: Mapped[str] = mapped_column(String(80), nullable=False)  # user id or service name
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now(), nullable=False)
