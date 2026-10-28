"""HTTP routes for the Transactions service (API_DESIGN.md)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.auth import current_user_id
from libs.db.categories import CATEGORY_NAMES
from libs.db.models import TransactionStatus
from libs.db.repositories import AccountRepository, TransactionRepository
from libs.db.session import get_session
from services.transactions import service
from services.transactions.imports import parse_csv
from services.transactions.schemas import (
    AccountRead,
    CSVImport,
    ImportResult,
    TransactionAccepted,
    TransactionCreate,
    TransactionPage,
    TransactionPatch,
    TransactionRead,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("/accounts", response_model=list[AccountRead])
async def list_accounts(
    session: AsyncSession = Depends(get_session), user_id: str = Depends(current_user_id)
) -> list[AccountRead]:
    return [
        AccountRead.model_validate(a)
        for a in await AccountRepository(session).list_for_user(user_id)
    ]


@router.get("/categories", response_model=dict[str, str])
async def list_categories(_user_id: str = Depends(current_user_id)) -> dict[str, str]:
    return CATEGORY_NAMES


@router.post("/import", response_model=ImportResult, status_code=202)
async def import_transactions(
    payload: CSVImport,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> ImportResult:
    account = await service.load_owned_account(session, payload.account_id, user_id)
    rows = parse_csv(payload.csv_text, account_id=account.id, currency=account.currency)
    created = 0
    ids = []
    for row in rows:
        result = await service.ingest_transaction(session, payload=row, user_id=user_id)
        created += int(result.created)
        ids.append(result.transaction.id)
    await session.commit()
    return ImportResult(created=created, skipped=len(rows) - created, transaction_ids=ids)


@router.post(
    "",
    response_model=TransactionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a transaction (categorization happens asynchronously)",
)
async def create_transaction(
    payload: TransactionCreate,
    response: Response,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> TransactionAccepted:
    result = await service.ingest_transaction(session, payload=payload, user_id=user_id)
    # The row, balance, audit entry and durable job are one commit. No provider or
    # Kafka call is made on the request path.
    await session.commit()
    if not result.created:
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
