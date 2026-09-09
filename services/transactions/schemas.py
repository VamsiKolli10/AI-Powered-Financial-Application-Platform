"""Request/response contracts for the Transactions service (see API_DESIGN.md)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from libs.db.categories import CATEGORY_NAMES, CATEGORY_SLUGS
from libs.db.models import CategorySource, Transaction, TransactionStatus


class TransactionCreate(BaseModel):
    account_id: str = Field(..., max_length=40)
    external_tx_id: str = Field(..., max_length=120)
    amount: Decimal = Field(..., description="Negative for outflow, positive for inflow")
    currency: str = Field("USD", min_length=3, max_length=3)
    description: str = Field(..., min_length=1, max_length=500)
    occurred_at: datetime

    @field_validator("amount")
    @classmethod
    def _non_zero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("amount must not be zero")
        return v.quantize(Decimal("0.01"))

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class TransactionPatch(BaseModel):
    """User correction of an AI-assigned category."""

    category: str

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        slug = v.strip().lower().replace(" & ", "_").replace(" ", "_")
        if slug not in CATEGORY_SLUGS:
            raise ValueError(f"unknown category '{v}'")
        return slug


class TransactionAccepted(BaseModel):
    """202 response - the write is durable, categorization is still pending."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: TransactionStatus
    amount: Decimal
    description: str
    occurred_at: datetime


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    external_tx_id: str
    status: TransactionStatus
    amount: Decimal
    currency: str
    description: str
    merchant: str | None = None
    category: str | None = None
    category_source: CategorySource | None = None
    anomaly_score: float | None = None
    occurred_at: datetime
    categorized_at: datetime | None = None

    @classmethod
    def from_model(cls, tx: Transaction) -> TransactionRead:
        return cls(
            id=tx.id,
            account_id=tx.account_id,
            external_tx_id=tx.external_tx_id,
            status=tx.status,
            amount=tx.amount,
            currency=tx.currency,
            description=tx.description,
            merchant=tx.merchant,
            category=CATEGORY_NAMES.get(tx.category_slug) if tx.category_slug else None,
            category_source=tx.category_source,
            anomaly_score=tx.anomaly_score,
            occurred_at=tx.occurred_at,
            categorized_at=tx.categorized_at,
        )


class TransactionPage(BaseModel):
    items: list[TransactionRead]
    next_cursor: str | None = None
    limit: int
