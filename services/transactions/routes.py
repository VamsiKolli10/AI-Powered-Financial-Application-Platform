"""HTTP routes for the Transactions service (API_DESIGN.md)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.auth import current_user_id
from libs.common.logging import get_logger
from libs.db.models import TransactionStatus
from libs.db.repositories import AccountRepository, TransactionRepository
from libs.db.session import get_session, session_scope
from libs.events.publisher import EventPublisher
from libs.events.schemas import BaseEvent
from services.transactions import service
from services.transactions.schemas import (
    TransactionAccepted,
    TransactionCreate,
    TransactionPage,
    TransactionPatch,
    TransactionRead,
)
from services.transactions.service import Classifier

log = get_logger("transactions")
router = APIRouter(prefix="/transactions", tags=["transactions"])

SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def _categorize_in_background(
    transaction_id: str,
    scope_factory: SessionScope = session_scope,
    classifier: Classifier | None = None,
    publisher: EventPublisher | None = None,
    anomaly_threshold: float = 0.8,
) -> None:
    """Categorize, commit, then publish.

    The session factory, classifier and publisher all come from app state so tests
    (and future standalone workers) can supply their own without touching the
    process-wide engine.
    """
    events: list[BaseEvent] = []
    try:
        async with scope_factory() as session:
            transaction = await service.categorize_transaction(
                session, transaction_id, classifier=classifier
            )
            if transaction is not None:
                events = await service.build_categorization_events(
                    session, transaction, anomaly_threshold=anomaly_threshold
                )
    except Exception as exc:  # noqa: BLE001 - background work must not crash the worker
        log.error("background_categorization_error", transaction_id=transaction_id, error=str(exc))
        return

    # Published only once the session above has committed, so no event can describe a
    # row that was rolled back.
    if publisher is not None:
        for event in events:
            await publisher.publish(event)


@router.post(
    "",
    response_model=TransactionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a transaction (categorization happens asynchronously)",
)
async def create_transaction(
    payload: TransactionCreate,
    request: Request,
    background: BackgroundTasks,
    response: Response,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> TransactionAccepted:
    result = await service.ingest_transaction(session, payload=payload, user_id=user_id)
    # Commit before scheduling: background tasks run before the request session's
    # teardown commit, so an uncommitted row would be invisible to the categorizer.
    await session.commit()
    if result.created:
        state = request.app.state
        background.add_task(
            _categorize_in_background,
            result.transaction.id,
            getattr(state, "session_scope", session_scope),
            getattr(state, "classifier", None),
            getattr(state, "publisher", None),
            getattr(state.settings, "anomaly_alert_threshold", 0.8),
        )
    else:
        # Idempotent replay: the row already exists, so this is not a new acceptance.
        response.status_code = status.HTTP_200_OK
    return TransactionAccepted.model_validate(result.transaction)


@router.get("", response_model=TransactionPage, summary="List transactions")
async def list_transactions(
    account_id: str | None = Query(default=None),
    category: str | None = Query(default=None),
    tx_status: TransactionStatus | None = Query(default=None, alias="status"),
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> TransactionPage:
    accounts = AccountRepository(session)
    if account_id:
        await service.load_owned_account(session, account_id, user_id)
        account_ids = [account_id]
    else:
        account_ids = await accounts.ids_for_user(user_id)

    rows, next_cursor = await TransactionRepository(session).list_page(
        account_ids=account_ids,
        limit=limit,
        cursor=cursor,
        category=category,
        status=tx_status,
        date_from=date_from,
        date_to=date_to,
    )
    return TransactionPage(
        items=[TransactionRead.from_model(tx) for tx in rows],
        next_cursor=next_cursor,
        limit=limit,
    )


@router.get("/{transaction_id}", response_model=TransactionRead, summary="Get one transaction")
async def get_transaction(
    transaction_id: str,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> TransactionRead:
    tx = await service.get_owned_transaction(
        session, transaction_id=transaction_id, user_id=user_id
    )
    return TransactionRead.from_model(tx)


@router.patch(
    "/{transaction_id}", response_model=TransactionRead, summary="Correct an assigned category"
)
async def patch_transaction(
    transaction_id: str,
    payload: TransactionPatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> TransactionRead:
    tx = await service.recategorize_by_user(
        session,
        transaction_id=transaction_id,
        category_slug=payload.category,
        user_id=user_id,
    )
    # The cached classification for this merchant produced a category the user rejected,
    # so drop it rather than serving the same wrong answer to the next transaction.
    classifier = getattr(request.app.state, "classifier", None)
    if classifier is not None:
        await classifier.invalidate(tx.normalized_description)
    return TransactionRead.from_model(tx)
