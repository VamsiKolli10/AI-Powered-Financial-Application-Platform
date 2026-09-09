"""Transaction ingestion and categorization logic.

Invariants this module protects:
  * The durable write never waits on an LLM call.
  * Ingestion is idempotent on (account_id, external_tx_id).
  * Only this service writes financial records.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.classification import Classification
from libs.common.errors import NotFoundError
from libs.common.logging import get_logger
from libs.common.text import guess_merchant, normalize_description
from libs.db.categories import FALLBACK_CATEGORY
from libs.db.models import (
    Account,
    CategorySource,
    Transaction,
    TransactionStatus,
)
from libs.db.repositories import (
    AccountRepository,
    AuditRepository,
    TransactionRepository,
)
from libs.events.schemas import (
    BaseEvent,
    TransactionAnomalyFlagged,
    TransactionCategorized,
)
from services.transactions import categorizer
from services.transactions.schemas import TransactionCreate

log = get_logger("transactions")

SERVICE_ACTOR = "transactions-service"

# Phase 3 supplies an LLM-backed implementation; without one the rules engine is used.
Classifier = Callable[[str, Decimal], Awaitable[Classification]]


@dataclass
class IngestResult:
    transaction: Transaction
    created: bool  # False when an idempotent replay returned the existing row


async def load_owned_account(session: AsyncSession, account_id: str, user_id: str) -> Account:
    account = await AccountRepository(session).get(account_id)
    # Same error either way: never reveal whether another user's account exists.
    if account is None or account.user_id != user_id:
        raise NotFoundError(f"Account '{account_id}' was not found.")
    return account


async def ingest_transaction(
    session: AsyncSession, *, payload: TransactionCreate, user_id: str
) -> IngestResult:
    """Write the raw transaction durably as `pending_categorization`."""
    account = await load_owned_account(session, payload.account_id, user_id)
    transactions = TransactionRepository(session)

    existing = await transactions.get_by_external_id(payload.account_id, payload.external_tx_id)
    if existing is not None:
        log.info(
            "ingest_idempotent_replay",
            transaction_id=existing.id,
            external_tx_id=payload.external_tx_id,
        )
        return IngestResult(existing, created=False)

    normalized = normalize_description(payload.description)
    transaction = Transaction(
        account_id=payload.account_id,
        external_tx_id=payload.external_tx_id,
        amount=payload.amount,
        currency=payload.currency or account.currency,
        description=payload.description,
        normalized_description=normalized,
        merchant=guess_merchant(normalized),
        status=TransactionStatus.PENDING_CATEGORIZATION,
        occurred_at=payload.occurred_at,
    )
    await transactions.add(transaction)
    await AccountRepository(session).adjust_balance(account, payload.amount)
    await AuditRepository(session).record(
        actor=user_id,
        action="transaction.ingested",
        entity_type="transaction",
        entity_id=transaction.id,
        payload={"amount": str(payload.amount), "external_tx_id": payload.external_tx_id},
    )
    log.info("transaction_ingested", transaction_id=transaction.id, account_id=account.id)
    return IngestResult(transaction, created=True)


async def categorize_transaction(
    session: AsyncSession, transaction_id: str, *, classifier: Classifier | None = None
) -> Transaction | None:
    """Apply a category and anomaly score to a pending transaction.

    `classifier` is an optional async callable (normalized_description, amount) ->
    Classification. Phase 3 passes the LLM-backed classifier here; without one this
    falls back to the deterministic rules engine.
    """
    transactions = TransactionRepository(session)
    transaction = await transactions.get(transaction_id)
    if transaction is None:
        log.warning("categorize_missing_transaction", transaction_id=transaction_id)
        return None
    if transaction.status is TransactionStatus.CATEGORIZED:
        return transaction

    try:
        if classifier is not None:
            result = await classifier(transaction.normalized_description, transaction.amount)
        else:
            result = categorizer.classify(transaction.normalized_description, transaction.amount)
        source = CategorySource.LLM if result.source == "llm" else CategorySource.RULES
        transaction.category_slug = result.category_slug
        transaction.category_source = source
    except Exception as exc:  # noqa: BLE001 - never leave a transaction stuck
        log.warning(
            "categorization_failed_using_fallback",
            transaction_id=transaction_id,
            error=str(exc),
        )
        fallback = categorizer.classify(transaction.normalized_description, transaction.amount)
        transaction.category_slug = fallback.category_slug or FALLBACK_CATEGORY
        transaction.category_source = CategorySource.RULES

    mean_amount, max_amount = await transactions.amount_stats(
        transaction.account_id, exclude_transaction_id=transaction.id
    )
    transaction.anomaly_score = categorizer.anomaly_score(
        Decimal(transaction.amount), mean_amount=mean_amount, max_amount=max_amount
    )
    transaction.status = TransactionStatus.CATEGORIZED
    transaction.categorized_at = datetime.now(UTC)

    await AuditRepository(session).record(
        actor=SERVICE_ACTOR,
        action="transaction.categorized",
        entity_type="transaction",
        entity_id=transaction.id,
        payload={
            "category": transaction.category_slug,
            "source": transaction.category_source.value if transaction.category_source else None,
            "anomaly_score": transaction.anomaly_score,
        },
    )
    log.info(
        "transaction_categorized",
        transaction_id=transaction.id,
        category=transaction.category_slug,
        anomaly_score=transaction.anomaly_score,
    )
    return transaction


async def recategorize_by_user(
    session: AsyncSession, *, transaction_id: str, category_slug: str, user_id: str
) -> Transaction:
    """User correction. Also the signal Phase 3 uses to invalidate the LLM cache entry."""
    transactions = TransactionRepository(session)
    transaction = await transactions.get(transaction_id)
    if transaction is None:
        raise NotFoundError(f"Transaction '{transaction_id}' was not found.")
    await load_owned_account(session, transaction.account_id, user_id)

    previous = transaction.category_slug
    transaction.category_slug = category_slug
    transaction.category_source = CategorySource.USER
    transaction.status = TransactionStatus.CATEGORIZED
    transaction.categorized_at = transaction.categorized_at or datetime.now(UTC)

    await AuditRepository(session).record(
        actor=user_id,
        action="transaction.recategorized",
        entity_type="transaction",
        entity_id=transaction.id,
        payload={"from": previous, "to": category_slug},
    )
    log.info(
        "transaction_recategorized",
        transaction_id=transaction.id,
        previous=previous,
        category=category_slug,
    )
    return transaction


async def get_owned_transaction(
    session: AsyncSession, *, transaction_id: str, user_id: str
) -> Transaction:
    transaction = await TransactionRepository(session).get(transaction_id)
    if transaction is None:
        raise NotFoundError(f"Transaction '{transaction_id}' was not found.")
    await load_owned_account(session, transaction.account_id, user_id)
    return transaction


async def build_categorization_events(
    session: AsyncSession, transaction: Transaction, *, anomaly_threshold: float = 0.8
) -> list[BaseEvent]:
    """Events describing a categorized transaction.

    Deliberately separate from the write: the caller publishes these *after* the
    session commits, so no event can ever describe a row that was rolled back. The
    inverse failure - committed but unpublished, if the broker is down - is the
    trade-off taken here; a transactional outbox would close it and is the natural
    next step if delivery ever needs to be guaranteed.
    """
    account = await AccountRepository(session).get(transaction.account_id)
    if account is None:  # pragma: no cover - FK makes this unreachable
        return []

    events: list[BaseEvent] = [
        TransactionCategorized(
            transaction_id=transaction.id,
            account_id=transaction.account_id,
            user_id=account.user_id,
            category=transaction.category_slug,
            category_source=(
                transaction.category_source.value if transaction.category_source else None
            ),
            amount=transaction.amount,
            currency=transaction.currency,
            merchant=transaction.merchant,
            anomaly_score=transaction.anomaly_score,
            occurred_at=transaction.occurred_at,
        )
    ]
    score = transaction.anomaly_score or 0.0
    if score >= anomaly_threshold:
        events.append(
            TransactionAnomalyFlagged(
                transaction_id=transaction.id,
                account_id=transaction.account_id,
                user_id=account.user_id,
                amount=transaction.amount,
                currency=transaction.currency,
                merchant=transaction.merchant,
                anomaly_score=score,
                reason="amount_outlier",
            )
        )
    return events
