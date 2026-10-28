"""Real PostgreSQL locks, Redis, Kafka delivery and restart/outage recovery.

Run only inside infra/tests/compose.yml. Fixtures belong to the disposable stack.
No access token, private config or provider error body is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select, update

from libs.common.config import get_settings
from libs.db.models import Account, CategorizationJob, Notification, OutboxEvent, Transaction
from libs.db.session import dispose_engine, get_sessionmaker
from libs.db.work import WorkRepository
from libs.llm_client.cache import ClassificationCache
from libs.llm_client.rate_limit import TokenBucketRateLimiter
from services.transactions.schemas import TransactionCreate
from services.transactions.service import ingest_transaction
from services.transactions.workers import TransactionWorkers


@asynccontextmanager
async def scope():
    async with get_sessionmaker()() as session, session.begin():
        yield session


def payload(account: str, external: str, amount: str = "-10.00") -> TransactionCreate:
    return TransactionCreate(
        account_id=account,
        external_tx_id=external,
        amount=Decimal(amount),
        currency="USD",
        description="Recovery market",
        occurred_at=datetime.now(UTC),
    )


async def register(client: httpx.AsyncClient) -> tuple[str, str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"infra-{uuid4().hex}@example.com",
            "password": "isolated-test-password",
            "full_name": "Infrastructure test",
        },
    )
    assert response.status_code == 201, "Registration failed"
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    me = (await client.get("/api/v1/auth/me")).json()
    account = (await client.get("/api/v1/transactions/accounts")).json()[0]
    return token, me["id"], account["id"]


async def eventually(check, *, seconds: float = 90) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if await check():
            return
        await asyncio.sleep(0.5)
    raise AssertionError("Timed out waiting for the expected persisted state")


async def delivered(transaction_id: str) -> bool:
    async with scope() as session:
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.transaction_id == transaction_id)
                )
            ).all()
        )
        alerts = list(
            (
                await session.scalars(
                    select(Notification).where(Notification.transaction_id == transaction_id)
                )
            ).all()
        )
        return (
            bool(events and alerts)
            and all(e.completed_at for e in events)
            and all(n.delivered_at for n in alerts)
            and len({n.dedupe_key for n in alerts}) == len(alerts)
        )


async def concurrency(client: httpx.AsyncClient) -> None:
    _, user, account = await register(client)

    # Concurrent request-shaped sessions must neither lose balance increments nor
    # create duplicate rows/jobs when the external id is replayed.
    async def ingest(external):
        async with scope() as session:
            return (
                await ingest_transaction(session, payload=payload(account, external), user_id=user)
            ).transaction.id

    ids = await asyncio.gather(*(ingest("replay") for _ in range(8)))
    assert len(set(ids)) == 1
    await asyncio.gather(*(ingest(f"distinct-{i}") for i in range(8)))
    async with scope() as session:
        assert (await session.get(Account, account)).balance == Decimal("-90.00")
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.account_id == account)
            )
            == 9
        )

    # Use a separate schema fixture whose jobs are held away from API pollers.
    async with scope() as session:
        future = datetime.now(UTC) + timedelta(days=1)
        await session.execute(
            update(CategorizationJob)
            .where(CategorizationJob.id.in_(ids))
            .values(
                completed_at=None,
                available_at=future,
                locked_until=None,
                lock_token=None,
                attempts=0,
            )
        )

    async def claim(now):
        async with scope() as session:
            return await WorkRepository(session, CategorizationJob).claim(now=now, lease_seconds=10)

    claims = await asyncio.gather(*(claim(future + timedelta(seconds=1)) for _ in range(12)))
    claimed_ids = [c.id for c in claims if c]
    assert len(claimed_ids) == len(set(claimed_ids)), "A job was claimed concurrently twice"
    target = next(c for c in claims if c and c.id == ids[0])
    async with scope() as session:
        repo = WorkRepository(session, CategorizationJob)
        assert not await repo.owns(target, now=future + timedelta(seconds=12))
        await session.execute(
            update(CategorizationJob)
            .where(CategorizationJob.id == target.id)
            .values(
                locked_until=datetime.now(UTC) - timedelta(seconds=1),
                available_at=datetime.now(UTC),
            )
        )
    # A fresh worker recovers the expired lease and cannot add a duplicate outbox event.
    assert await TransactionWorkers(scope).categorize_once()
    async with scope() as session:
        assert (await session.get(CategorizationJob, target.id)).completed_at is not None

    async def no_pending_events():
        async with scope() as session:
            return not await session.scalar(
                select(OutboxEvent.id).where(OutboxEvent.completed_at.is_(None)).limit(1)
            )

    await eventually(no_pending_events)
    # Future availability keeps API pollers away while real concurrent PG claims
    # prove that a leased first event blocks only its own account.
    stamp = datetime.now(UTC) + timedelta(days=2)
    prefix = uuid4().hex[:10]
    first, second, independent = [f"evt_{prefix}_{i}" for i in range(3)]
    async with scope() as session:
        for index, event_id in enumerate((first, second, independent)):
            session.add(
                OutboxEvent(
                    id=event_id,
                    transaction_id=target.id,
                    event_type=f"test.order.{index}",
                    partition_key=account if index < 2 else f"isolated-{prefix}",
                    payload={},
                    created_at=stamp + timedelta(seconds=index),
                    available_at=stamp,
                )
            )

    async def event_claim():
        async with scope() as session:
            return await WorkRepository(session, OutboxEvent).claim(
                now=stamp + timedelta(seconds=5)
            )

    claims = await asyncio.gather(*(event_claim() for _ in range(8)))
    assert {c.id for c in claims if c} == {first, independent}
    async with scope() as session:
        for claim in claims:
            if claim:
                await WorkRepository(session, OutboxEvent).complete(claim)
    next_event = await event_claim()
    assert next_event and next_event.id == second
    async with scope() as session:
        await WorkRepository(session, OutboxEvent).complete(next_event)

    redis = Redis.from_url(get_settings().redis_url)
    try:
        assert await redis.ping()
        cache = ClassificationCache(redis, prompt_version=f"test-{uuid4().hex}")
        await cache.set("synthetic merchant", {"category": "groceries"})
        assert await cache.get("synthetic merchant") == {"category": "groceries"}
        await cache.invalidate("synthetic merchant")
        assert await cache.get("synthetic merchant") is None
        key = f"test:ratelimit:{uuid4().hex}"
        limiter = TokenBucketRateLimiter(redis, key=key, per_minute=1, burst=1)
        assert (await limiter.acquire())[0]
        assert not (await limiter.acquire())[0]
        await redis.delete(key)
    finally:
        await redis.aclose()
    print(
        "PASS PostgreSQL concurrent ingestion, exclusive claims, lease fencing, recovery, "
        "account FIFO; Redis cache and rate limiting"
    )


async def recovery(client: httpx.AsyncClient, *, stage: bool, outage: bool) -> None:
    fixture = Path("/tmp/finai-outage.json" if outage else "/tmp/finai-recovery.json")
    if stage:
        # While the API is stopped, look up the registered user's account in the DB.
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"recovery-{uuid4().hex}@example.com",
                "password": "isolated-test-password",
            },
        )
        assert response.status_code == 201
        token = response.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        user = (await client.get("/api/v1/auth/me")).json()["id"]
        async with scope() as session:
            account = await session.scalar(select(Account.id).where(Account.user_id == user))
            result = await ingest_transaction(
                session, payload=payload(account, uuid4().hex, "-900.00"), user_id=user
            )
            tx_id = result.transaction.id
        await asyncio.to_thread(fixture.write_text, json.dumps({"transaction_id": tx_id}))
        await asyncio.to_thread(fixture.chmod, 0o600)
        if outage:

            async def pending_outbox():
                async with scope() as session:
                    rows = list(
                        (
                            await session.scalars(
                                select(OutboxEvent).where(OutboxEvent.transaction_id == tx_id)
                            )
                        ).all()
                    )
                    return bool(rows) and all(r.completed_at is None for r in rows)

            await eventually(pending_outbox)
        print(
            "PASS committed work persists during " + ("broker outage" if outage else "API shutdown")
        )
    else:
        tx_id = json.loads(await asyncio.to_thread(fixture.read_text))["transaction_id"]
        await eventually(lambda: delivered(tx_id))
        # Force an acknowledged event to replay with its original identity. Kafka
        # delivery remains at least once; stored alerts must remain idempotent.
        async with scope() as session:
            before = await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.transaction_id == tx_id)
            )
            event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.transaction_id == tx_id)
            )
            event.completed_at = None
            event.available_at = datetime.now(UTC)
            event.attempts = 0
        await eventually(lambda: delivered(tx_id))
        await asyncio.sleep(2)
        async with scope() as session:
            after = await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.transaction_id == tx_id)
            )
            assert before == after
        await asyncio.to_thread(fixture.unlink)
        print(
            "PASS "
            + ("broker outage" if outage else "API restart")
            + " recovery and acknowledged Kafka → notification delivery"
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    for flag in ("stage-recovery", "check-recovery", "stage-outage", "check-outage"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.environment != "test" or not settings.database_url.endswith("/finai_e2e"):
        raise RuntimeError("This runner requires the isolated finai_e2e database")
    try:
        async with httpx.AsyncClient(base_url="http://gateway:8000", timeout=45) as client:
            if any(vars(args).values()):
                await recovery(
                    client,
                    stage=args.stage_recovery or args.stage_outage,
                    outage=args.stage_outage or args.check_outage,
                )
            else:
                await concurrency(client)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
