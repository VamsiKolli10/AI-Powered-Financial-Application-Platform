"""Event contracts between services (API_DESIGN.md, "Internal Event Schemas").

These are a published interface: consumers deployed independently will read events
produced by older and newer versions of the producer. So:

  * add fields, never repurpose or remove them
  * every event carries `schema_version`
  * consumers ignore unknown fields rather than failing (`extra="ignore"`)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class Topics:
    TRANSACTIONS = "transactions.events"


class BaseEvent(BaseModel):
    """Envelope shared by every event."""

    model_config = ConfigDict(extra="ignore")

    event_type: str
    schema_version: int = SCHEMA_VERSION
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def partition_key(self) -> str:
        """Events for one account must stay ordered, so they share a partition."""
        return getattr(self, "account_id", "")


class TransactionCategorized(BaseEvent):
    event_type: Literal["transaction.categorized"] = "transaction.categorized"

    transaction_id: str
    account_id: str
    user_id: str
    category: str | None = None
    category_source: str | None = None
    amount: Decimal
    currency: str = "USD"
    merchant: str | None = None
    anomaly_score: float | None = None
    occurred_at: datetime


class TransactionAnomalyFlagged(BaseEvent):
    event_type: Literal["transaction.anomaly_flagged"] = "transaction.anomaly_flagged"

    transaction_id: str
    account_id: str
    user_id: str
    amount: Decimal
    currency: str = "USD"
    merchant: str | None = None
    anomaly_score: float
    reason: str


EVENT_TYPES: dict[str, type[BaseEvent]] = {
    "transaction.categorized": TransactionCategorized,
    "transaction.anomaly_flagged": TransactionAnomalyFlagged,
}


def parse_event(payload: dict) -> BaseEvent | None:
    """Decode one event, or None if this consumer does not know the type.

    An unknown type is normal during a rolling deploy - the producer may be ahead of
    this consumer - so it is skipped rather than treated as a poison message.
    """
    model = EVENT_TYPES.get(str(payload.get("event_type", "")))
    if model is None:
        return None
    return model.model_validate(payload)
