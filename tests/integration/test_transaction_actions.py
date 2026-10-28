"""New browser-facing write paths share validation, ownership and atomicity."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from libs.db.models import Account, CategorizationJob, Transaction
from services.transactions.main import app


async def test_accounts_and_categories_do_not_expose_other_users(make_client):
    client = await make_client(app)
    assert [a["id"] for a in (await client.get("/transactions/accounts")).json()] == ["acct_test"]
    assert (await client.get("/transactions/categories")).json()["groceries"] == "Groceries"


async def test_csv_is_atomic_and_reimport_is_idempotent(make_client, session):
    client = await make_client(app)
    csv = (
        'occurred_at,description,amount\n2026-10-01,"Market, produce",-20.50\n'
        "2026-10-02,Salary,1000.00\n"
    )
    result = await client.post(
        "/transactions/import", json={"account_id": "acct_test", "csv_text": csv}
    )
    assert result.status_code == 202
    assert result.json()["created"] == 2
    assert (
        await client.post("/transactions/import", json={"account_id": "acct_test", "csv_text": csv})
    ).json()["skipped"] == 2
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 2
    assert await session.scalar(select(func.count()).select_from(CategorizationJob)) == 2
    session.expire_all()
    assert (await session.get(Account, "acct_test")).balance == Decimal("979.50")


@pytest.mark.parametrize(
    "csv",
    [
        "occurred_at,description,amount\n2026-10-01,First,-10\n2026-10-02,Bad,0\n",
        "occurred_at,description,amount\n2026-10-01,Bad,NaN\n",
        "occurred_at,description,amount\n2026-10-01,Bad,-0.001\n",
        'occurred_at,description,amount\n2026-10-01,"unclosed,-10\n',
        "date,description,amount\n2026-10-01,Bad,-10\n",
        "occurred_at,description,amount\n",
    ],
)
async def test_invalid_csv_writes_nothing(make_client, session, csv):
    client = await make_client(app)
    result = await client.post(
        "/transactions/import", json={"account_id": "acct_test", "csv_text": csv}
    )
    assert result.status_code == 422
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 0


async def test_import_rejects_another_users_account(make_client):
    client = await make_client(app)
    result = await client.post(
        "/transactions/import",
        json={
            "account_id": "acct_other",
            "csv_text": "occurred_at,description,amount\n2026-10-01,Other,-10\n",
        },
    )
    assert result.status_code == 404
