"""Atomic, leased claims for durable jobs and the transactional event outbox."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from libs.db.ids import new_id
from libs.db.models import CategorizationJob, OutboxEvent

WorkModel = type[CategorizationJob] | type[OutboxEvent]


@dataclass(frozen=True)
class WorkClaim:
    id: str
    token: str
    attempts: int


class WorkRepository:
    def __init__(self, session: AsyncSession, model: WorkModel) -> None:
        self.session = session
        self.model = model

    async def claim(
        self, *, lease_seconds: float = 120, now: datetime | None = None, max_attempts: int = 10
    ) -> WorkClaim | None:
        now = now or datetime.now(UTC)
        model = self.model
        eligible = and_(
            model.completed_at.is_(None),
            model.attempts < max_attempts,
            model.available_at <= now,
            or_(model.locked_until.is_(None), model.locked_until <= now),
        )
        candidate = select(model.id).where(eligible)
        if model is OutboxEvent:
            # Keep an account's events in order while letting other accounts advance.
            prior = aliased(OutboxEvent)
            candidate = candidate.where(
                ~exists().where(
                    prior.partition_key == OutboxEvent.partition_key,
                    prior.completed_at.is_(None),
                    or_(
                        prior.created_at < OutboxEvent.created_at,
                        and_(prior.created_at == OutboxEvent.created_at, prior.id < OutboxEvent.id),
                    ),
                )
            )
        candidate_id = (
            candidate.order_by(model.created_at, model.id)
            .correlate(None)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        token = new_id("lease")
        # A single statement also protects the SQLite test path, where FOR UPDATE
        # is unavailable. PostgreSQL skips rows held by other claimers.
        row = (
            await self.session.execute(
                update(model)
                .where(model.id == candidate_id, eligible)
                .values(
                    locked_until=now + timedelta(seconds=lease_seconds),
                    lock_token=token,
                    attempts=model.attempts + 1,
                )
                .returning(model.id, model.attempts)
                .execution_options(synchronize_session=False)
            )
        ).one_or_none()
        return WorkClaim(row[0], token, row[1]) if row is not None else None

    async def owns(self, claim: WorkClaim, *, now: datetime | None = None) -> bool:
        """Lock before applying results; an expired or superseded worker cannot write."""
        model = self.model
        row = await self.session.scalar(
            select(model.id)
            .where(
                model.id == claim.id,
                model.lock_token == claim.token,
                model.completed_at.is_(None),
                model.locked_until > (now or datetime.now(UTC)),
            )
            .with_for_update()
        )
        return row is not None

    async def complete(self, claim: WorkClaim) -> None:
        model = self.model
        await self.session.execute(
            update(model)
            .where(model.id == claim.id, model.lock_token == claim.token)
            .values(
                completed_at=datetime.now(UTC), locked_until=None, lock_token=None, last_error=None
            )
        )

    async def retry(self, claim: WorkClaim, error: Exception) -> None:
        model = self.model
        await self.session.execute(
            update(model)
            .where(
                model.id == claim.id, model.lock_token == claim.token, model.completed_at.is_(None)
            )
            .values(
                available_at=datetime.now(UTC)
                + timedelta(seconds=min(2 ** min(claim.attempts, 6), 60)),
                locked_until=None,
                lock_token=None,
                # Persist the type, never provider errors or request payloads.
                last_error=type(error).__name__[:80],
            )
        )
