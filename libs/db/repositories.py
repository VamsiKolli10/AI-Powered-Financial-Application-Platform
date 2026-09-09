"""Repository layer. All database access goes through here, never raw queries in routes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.models import (
    Account,
    AuditLog,
    Category,
    Notification,
    NotificationType,
    Transaction,
    TransactionStatus,
    User,
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: str) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def create(self, *, email: str, hashed_password: str, full_name: str | None) -> User:
        user = User(email=email.lower(), hashed_password=hashed_password, full_name=full_name)
        self.session.add(user)
        await self.session.flush()
        return user


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, account_id: str) -> Account | None:
        return await self.session.get(Account, account_id)

    async def list_for_user(self, user_id: str) -> list[Account]:
        result = await self.session.execute(select(Account).where(Account.user_id == user_id))
        return list(result.scalars())

    async def ids_for_user(self, user_id: str) -> list[str]:
        result = await self.session.execute(select(Account.id).where(Account.user_id == user_id))
        return list(result.scalars())

    async def create(
        self, *, user_id: str, name: str, institution: str | None = None, currency: str = "USD"
    ) -> Account:
        account = Account(user_id=user_id, name=name, institution=institution, currency=currency)
        self.session.add(account)
        await self.session.flush()
        return account

    async def adjust_balance(self, account: Account, delta: Decimal) -> None:
        account.balance = (account.balance or Decimal("0")) + delta


class TransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, transaction_id: str) -> Transaction | None:
        return await self.session.get(Transaction, transaction_id)

    async def get_by_external_id(self, account_id: str, external_tx_id: str) -> Transaction | None:
        result = await self.session.execute(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.external_tx_id == external_tx_id,
            )
        )
        return result.scalar_one_or_none()

    async def add(self, transaction: Transaction) -> Transaction:
        self.session.add(transaction)
        await self.session.flush()
        return transaction

    def _base_query(
        self,
        *,
        account_ids: list[str],
        category: str | None = None,
        status: TransactionStatus | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> Select[tuple[Transaction]]:
        stmt = select(Transaction).where(Transaction.account_id.in_(account_ids))
        if category:
            stmt = stmt.where(Transaction.category_slug == category)
        if status:
            stmt = stmt.where(Transaction.status == status)
        if date_from:
            stmt = stmt.where(Transaction.occurred_at >= date_from)
        if date_to:
            stmt = stmt.where(Transaction.occurred_at <= date_to)
        return stmt

    async def list_page(
        self,
        *,
        account_ids: list[str],
        limit: int = 25,
        cursor: str | None = None,
        **filters: Any,
    ) -> tuple[list[Transaction], str | None]:
        """Keyset pagination on (occurred_at, id) descending."""
        if not account_ids:
            return [], None
        stmt = self._base_query(account_ids=account_ids, **filters)
        if cursor:
            occurred_raw, _, cursor_id = cursor.partition("|")
            occurred = datetime.fromisoformat(occurred_raw)
            stmt = stmt.where(
                (Transaction.occurred_at < occurred)
                | ((Transaction.occurred_at == occurred) & (Transaction.id < cursor_id))
            )
        stmt = stmt.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()).limit(limit + 1)
        rows = list((await self.session.execute(stmt)).scalars())
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            next_cursor = f"{last.occurred_at.isoformat()}|{last.id}"
        return rows, next_cursor

    async def list_pending(self, limit: int = 100) -> list[Transaction]:
        stmt = (
            select(Transaction)
            .where(Transaction.status == TransactionStatus.PENDING_CATEGORIZATION)
            .order_by(Transaction.created_at)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def spend_by_category(
        self, *, account_ids: list[str], date_from: datetime, date_to: datetime
    ) -> list[tuple[str | None, Decimal, int]]:
        """Aggregate outflow per category. Only aggregates are ever sent to the LLM."""
        if not account_ids:
            return []
        stmt = (
            select(
                Transaction.category_slug,
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("count"),
            )
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.occurred_at >= date_from,
                Transaction.occurred_at <= date_to,
                Transaction.amount < 0,
            )
            .group_by(Transaction.category_slug)
            .order_by(func.sum(Transaction.amount))
        )
        rows = (await self.session.execute(stmt)).all()
        return [(slug, abs(Decimal(str(total))), int(count)) for slug, total, count in rows]

    async def amount_stats(
        self, account_id: str, *, exclude_transaction_id: str | None = None
    ) -> tuple[Decimal, Decimal]:
        """(mean, max) absolute outflow for an account - input to anomaly scoring.

        The transaction being scored is excluded: including it in its own baseline
        drags the mean toward the outlier and hides exactly what we are looking for.
        """
        stmt = select(
            func.avg(func.abs(Transaction.amount)), func.max(func.abs(Transaction.amount))
        ).where(Transaction.account_id == account_id, Transaction.amount < 0)
        if exclude_transaction_id is not None:
            stmt = stmt.where(Transaction.id != exclude_transaction_id)
        avg, mx = (await self.session.execute(stmt)).one()
        return Decimal(str(avg or 0)), Decimal(str(mx or 0))


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[Category]:
        return list((await self.session.execute(select(Category))).scalars())

    async def exists(self, slug: str) -> bool:
        return await self.session.get(Category, slug) is not None


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_if_new(
        self,
        *,
        user_id: str,
        type_: NotificationType,
        message: str,
        dedupe_key: str,
        transaction_id: str | None = None,
    ) -> Notification | None:
        """Insert an alert, or return None if this exact alert already exists.

        Kafka delivers at least once, so this is the idempotency boundary for the
        whole Notifications service.
        """
        existing = await self.session.execute(
            select(Notification).where(Notification.dedupe_key == dedupe_key)
        )
        if existing.scalar_one_or_none() is not None:
            return None
        notification = Notification(
            user_id=user_id,
            type=type_,
            message=message,
            dedupe_key=dedupe_key,
            transaction_id=transaction_id,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def list_for_user(self, user_id: str, limit: int = 50) -> list[Notification]:
        stmt = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get(self, notification_id: str) -> Notification | None:
        return await self.session.get(Notification, notification_id)


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload: dict | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
            )
        )
