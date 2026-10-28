"""Recovery guarantees for persisted enrichment jobs and event delivery."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from libs.common.classification import Classification
from libs.db.models import AuditLog, CategorizationJob, Notification, OutboxEvent, Transaction
from libs.db.work import WorkRepository
from libs.events import InMemoryEventPublisher
from services.notifications.delivery import CollectingChannel
from services.notifications.handlers import NotificationHandler
from services.notifications.rules import AlertThresholds
from services.transactions import service
from services.transactions.schemas import TransactionCreate
from services.transactions.workers import TransactionWorkers
from tests.conftest import OTHER_USER_ID, TEST_USER_ID


async def ingest(session, payload, *, user=TEST_USER_ID, **overrides):
    result = await service.ingest_transaction(
        session, payload=TransactionCreate(**{**payload, **overrides}), user_id=user
    )
    await session.commit()
    return result.transaction.id


async def make_due(session, model):
    await session.execute(
        update(model).values(available_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()


async def test_transaction_and_job_roll_back_together(session, tx_payload):
    result = await service.ingest_transaction(
        session, payload=TransactionCreate(**tx_payload), user_id=TEST_USER_ID
    )
    tx_id = result.transaction.id
    await session.flush()
    assert await session.get(CategorizationJob, tx_id) is not None
    await session.rollback()
    assert await session.get(Transaction, tx_id) is None
    assert await session.get(CategorizationJob, tx_id) is None


async def test_claim_is_exclusive_and_expired_worker_is_fenced(session, tx_payload):
    await ingest(session, tx_payload)
    repo = WorkRepository(session, CategorizationJob)
    now = datetime.now(UTC)
    first = await repo.claim(now=now, lease_seconds=10)
    await session.commit()
    assert await repo.claim(now=now + timedelta(seconds=1)) is None
    second = await repo.claim(now=now + timedelta(seconds=11))
    await session.commit()
    assert second.id == first.id
    assert second.token != first.token and second.attempts == 2
    assert not await repo.owns(first)
    assert await repo.owns(second)


async def test_poison_work_parks_after_bounded_attempts(session, tx_payload):
    tx_id = await ingest(session, tx_payload)
    await session.execute(
        update(CategorizationJob).where(CategorizationJob.id == tx_id).values(attempts=10)
    )
    await session.commit()
    repo = WorkRepository(session, CategorizationJob)
    assert await repo.claim() is None
    # An operator can resume the specific parked job after fixing its cause.
    await session.execute(
        update(CategorizationJob).where(CategorizationJob.id == tx_id).values(attempts=0)
    )
    await session.commit()
    assert (await repo.claim()).id == tx_id


async def test_cancelled_worker_is_recovered_by_a_fresh_worker(
    session, session_scope_factory, tx_payload
):
    tx_id = await ingest(session, tx_payload)
    entered = asyncio.Event()

    async def interrupted(description, amount):
        entered.set()
        await asyncio.Event().wait()

    old_worker = TransactionWorkers(session_scope_factory, classifier=interrupted)
    task = asyncio.create_task(old_worker.categorize_once())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await session.execute(
        update(CategorizationJob).values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    fresh_worker = TransactionWorkers(session_scope_factory)
    assert await fresh_worker.categorize_once()
    await session.refresh(await session.get(Transaction, tx_id))
    transaction = await session.get(Transaction, tx_id)
    assert transaction.status.value == "categorized"
    job = await session.get(CategorizationJob, tx_id)
    assert job.attempts == 2 and job.completed_at is not None
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


async def test_user_correction_during_provider_call_is_not_overwritten(
    session, session_scope_factory, tx_payload
):
    tx_id = await ingest(session, tx_payload)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(description, amount):
        entered.set()
        await release.wait()
        return Classification(category_slug="electronics", confidence=0.9, source="llm")

    worker = TransactionWorkers(session_scope_factory, classifier=delayed)
    task = asyncio.create_task(worker.categorize_once())
    await asyncio.wait_for(entered.wait(), timeout=1)
    # This commits while the provider call is running: no long database lock.
    async with session_scope_factory() as correction:
        await service.recategorize_by_user(
            correction, transaction_id=tx_id, category_slug="groceries", user_id=TEST_USER_ID
        )
    release.set()
    await task
    transaction = await session.get(Transaction, tx_id, populate_existing=True)
    assert transaction.category_slug == "groceries"
    assert transaction.category_source.value == "user"
    event = await session.scalar(select(OutboxEvent))
    assert event.payload["category"] == "groceries"


async def test_database_failure_rolls_back_enrichment_and_events_and_retries(
    session, session_scope_factory, tx_payload, monkeypatch
):
    tx_id = await ingest(session, tx_payload)
    build_events = service.build_categorization_events

    async def fail_build(*args, **kwargs):
        raise RuntimeError("Synthetic failure after enrichment")

    monkeypatch.setattr(service, "build_categorization_events", fail_build)
    worker = TransactionWorkers(session_scope_factory)
    assert await worker.categorize_once()
    transaction = await session.get(Transaction, tx_id, populate_existing=True)
    assert transaction.status.value == "pending_categorization"
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    job = await session.get(CategorizationJob, tx_id)
    assert job.completed_at is None and job.last_error == "RuntimeError"
    monkeypatch.setattr(service, "build_categorization_events", build_events)
    await make_due(session, CategorizationJob)
    assert await TransactionWorkers(session_scope_factory).categorize_once()
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    assert (
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "transaction.categorized")
        )
        == 1
    )


async def test_broker_failure_keeps_event_until_acknowledged(
    session, session_scope_factory, tx_payload
):
    await ingest(session, tx_payload)
    await TransactionWorkers(session_scope_factory).categorize_once()
    record = await session.scalar(select(OutboxEvent))
    event_id = record.id

    class UnavailablePublisher(InMemoryEventPublisher):
        async def publish(self, event, **kwargs):
            raise ConnectionError("Synthetic broker outage")

    assert await TransactionWorkers(
        session_scope_factory, publisher=UnavailablePublisher()
    ).publish_once()
    await session.refresh(record)
    assert record.completed_at is None and record.last_error == "ConnectionError"
    await make_due(session, OutboxEvent)
    recovered = InMemoryEventPublisher()
    assert await TransactionWorkers(session_scope_factory, publisher=recovered).publish_once()
    await session.refresh(record)
    assert record.completed_at is not None and record.attempts == 2
    assert recovered.events[0].event_id == event_id


async def test_publish_then_commit_failure_replays_without_duplicate_alerts(
    session, session_scope_factory, tx_payload, monkeypatch
):
    await ingest(session, tx_payload, amount="-700.00")
    await TransactionWorkers(session_scope_factory).categorize_once()
    channel = CollectingChannel()
    handler = NotificationHandler(
        session_scope=session_scope_factory,
        channel=channel,
        thresholds=AlertThresholds(large_transaction=Decimal("500")),
    )
    publisher = InMemoryEventPublisher(handlers=[handler])
    original = WorkRepository.complete
    failed = False

    async def fail_completion(repo, claim):
        nonlocal failed
        if repo.model is OutboxEvent and not failed:
            failed = True
            raise RuntimeError("Synthetic failure after publishing")
        await original(repo, claim)

    monkeypatch.setattr(WorkRepository, "complete", fail_completion)
    worker = TransactionWorkers(session_scope_factory, publisher=publisher)
    await worker.publish_once()
    await make_due(session, OutboxEvent)
    await worker.publish_once()
    assert len(publisher.events) == 2
    assert publisher.events[0].event_id == publisher.events[1].event_id
    assert await session.scalar(select(func.count()).select_from(Notification)) == 1
    assert len(channel.sent) == 1


async def test_outbox_preserves_account_order_without_blocking_other_accounts(
    session, session_scope_factory, tx_payload
):
    first_id = await ingest(session, tx_payload, external_tx_id="order_1")
    await ingest(session, tx_payload, external_tx_id="order_2")
    other_id = await ingest(
        session, tx_payload, user=OTHER_USER_ID, account_id="acct_other", external_tx_id="other"
    )
    worker = TransactionWorkers(session_scope_factory)
    while await worker.categorize_once():
        pass
    repo = WorkRepository(session, OutboxEvent)
    first = await repo.claim()
    await session.commit()
    assert (await session.get(OutboxEvent, first.id)).transaction_id == first_id
    second = await repo.claim()
    await session.commit()
    assert (await session.get(OutboxEvent, second.id)).transaction_id == other_id
    assert await repo.claim() is None  # same-account second event waits for the first
