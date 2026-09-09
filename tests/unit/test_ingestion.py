"""Service-layer tests for ingestion, idempotency and categorization."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from libs.common.errors import NotFoundError
from libs.db.models import CategorySource, TransactionStatus
from libs.db.repositories import AccountRepository, TransactionRepository
from services.transactions import service
from services.transactions.schemas import TransactionCreate
from tests.conftest import OTHER_USER_ID, TEST_USER_ID


def _payload(**overrides) -> TransactionCreate:
    base = {
        "account_id": "acct_test",
        "external_tx_id": "bank_tx_1",
        "amount": Decimal("-42.50"),
        "currency": "USD",
        "description": "STARBUCKS #1234 SEATTLE WA",
        "occurred_at": datetime(2026, 9, 5, 14, 30, tzinfo=UTC),
    }
    return TransactionCreate(**{**base, **overrides})


async def test_ingest_writes_pending_row_without_ai(session):
    result = await service.ingest_transaction(session, payload=_payload(), user_id=TEST_USER_ID)

    assert result.created is True
    tx = result.transaction
    assert tx.status is TransactionStatus.PENDING_CATEGORIZATION
    assert tx.category_slug is None
    assert tx.normalized_description == "starbucks"
    assert tx.id.startswith("tx_")


async def test_ingest_is_idempotent_on_external_id(session):
    first = await service.ingest_transaction(session, payload=_payload(), user_id=TEST_USER_ID)
    await session.commit()
    second = await service.ingest_transaction(session, payload=_payload(), user_id=TEST_USER_ID)

    assert second.created is False
    assert second.transaction.id == first.transaction.id


async def test_ingest_updates_account_balance(session):
    account = await AccountRepository(session).get("acct_test")
    before = account.balance
    await service.ingest_transaction(session, payload=_payload(), user_id=TEST_USER_ID)
    assert account.balance == before + Decimal("-42.50")


async def test_ingest_rejects_another_users_account(session):
    with pytest.raises(NotFoundError):
        await service.ingest_transaction(
            session, payload=_payload(account_id="acct_other"), user_id=TEST_USER_ID
        )


async def test_categorization_moves_row_to_categorized(session):
    result = await service.ingest_transaction(session, payload=_payload(), user_id=TEST_USER_ID)
    await session.commit()

    tx = await service.categorize_transaction(session, result.transaction.id)

    assert tx.status is TransactionStatus.CATEGORIZED
    assert tx.category_slug == "dining_coffee"
    assert tx.category_source is CategorySource.RULES
    assert tx.categorized_at is not None
    assert 0.0 <= tx.anomaly_score <= 1.0


async def test_categorization_is_idempotent(session):
    result = await service.ingest_transaction(session, payload=_payload(), user_id=TEST_USER_ID)
    await session.commit()
    first = await service.categorize_transaction(session, result.transaction.id)
    stamp = first.categorized_at
    second = await service.categorize_transaction(session, result.transaction.id)
    assert second.categorized_at == stamp


async def test_categorization_falls_back_when_classifier_fails(session):
    """The circuit breaker: an LLM outage must not leave a transaction uncategorized."""
    result = await service.ingest_transaction(session, payload=_payload(), user_id=TEST_USER_ID)
    await session.commit()

    async def broken_classifier(_desc, _amount):
        raise RuntimeError("openai is down")

    tx = await service.categorize_transaction(
        session, result.transaction.id, classifier=broken_classifier
    )

    assert tx.status is TransactionStatus.CATEGORIZED
    assert tx.category_source is CategorySource.RULES
    assert tx.category_slug == "dining_coffee"


async def test_outlier_is_not_hidden_by_its_own_amount(session):
    """Regression: a transaction must not be part of the baseline it is scored against."""
    for i in range(6):
        result = await service.ingest_transaction(
            session,
            payload=_payload(external_tx_id=f"normal_{i}", amount=Decimal("-12.50")),
            user_id=TEST_USER_ID,
        )
        await session.commit()
        await service.categorize_transaction(session, result.transaction.id)

    outlier = await service.ingest_transaction(
        session,
        payload=_payload(external_tx_id="outlier", amount=Decimal("-4820.00")),
        user_id=TEST_USER_ID,
    )
    await session.commit()
    tx = await service.categorize_transaction(session, outlier.transaction.id)

    assert tx.anomaly_score >= 0.8


async def test_user_correction_overrides_source(session):
    result = await service.ingest_transaction(session, payload=_payload(), user_id=TEST_USER_ID)
    await session.commit()
    await service.categorize_transaction(session, result.transaction.id)

    tx = await service.recategorize_by_user(
        session,
        transaction_id=result.transaction.id,
        category_slug="groceries",
        user_id=TEST_USER_ID,
    )

    assert tx.category_slug == "groceries"
    assert tx.category_source is CategorySource.USER


async def test_user_cannot_correct_another_users_transaction(session):
    result = await service.ingest_transaction(session, payload=_payload(), user_id=TEST_USER_ID)
    await session.commit()

    with pytest.raises(NotFoundError):
        await service.recategorize_by_user(
            session,
            transaction_id=result.transaction.id,
            category_slug="groceries",
            user_id=OTHER_USER_ID,
        )


async def test_pagination_returns_cursor(session):
    for i in range(5):
        await service.ingest_transaction(
            session,
            payload=_payload(
                external_tx_id=f"bank_tx_{i}",
                occurred_at=datetime(2026, 9, i + 1, 12, 0, tzinfo=UTC),
            ),
            user_id=TEST_USER_ID,
        )
    await session.commit()

    repo = TransactionRepository(session)
    page1, cursor = await repo.list_page(account_ids=["acct_test"], limit=2)
    assert len(page1) == 2 and cursor
    page2, _ = await repo.list_page(account_ids=["acct_test"], limit=2, cursor=cursor)
    assert {t.id for t in page1}.isdisjoint({t.id for t in page2})
    # Newest first.
    assert page1[0].occurred_at > page2[0].occurred_at
