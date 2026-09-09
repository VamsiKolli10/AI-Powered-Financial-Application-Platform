"""Seed the database with the canonical categories and a demo user's transaction history.

    python -m scripts.seed --months 6 --transactions 400

Idempotent: re-running upserts categories and skips the demo user if it already exists.
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from libs.common.logging import configure_logging, get_logger
from libs.common.security import hash_password
from libs.common.text import guess_merchant, normalize_description
from libs.db.categories import CATEGORIES
from libs.db.models import Category, CategorySource, Transaction, TransactionStatus
from libs.db.repositories import AccountRepository, UserRepository
from libs.db.session import dispose_engine, session_scope
from services.transactions import categorizer

log = get_logger("seed")

# Not a .local address: that is a reserved special-use domain, and email
# validation on /auth/login rejects it - the demo user must be able to log in.
DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password"

# (description template, amount range, weight) - realistic enough to demo categorization.
MERCHANTS: list[tuple[str, float, float, int]] = [
    ("STARBUCKS #{n} SEATTLE WA", 4.0, 12.0, 14),
    ("BLUE BOTTLE COFFEE {n}", 5.0, 14.0, 5),
    ("DOORDASH*CHIPOTLE", 18.0, 45.0, 8),
    ("SQ *TACO STAND", 12.0, 30.0, 5),
    ("WHOLE FOODS MKT #{n}", 40.0, 190.0, 10),
    ("TRADER JOE'S #{n}", 25.0, 120.0, 8),
    ("SAFEWAY STORE {n}", 30.0, 140.0, 5),
    ("UBER *TRIP {n}", 8.0, 46.0, 9),
    ("LYFT *RIDE", 7.0, 40.0, 5),
    ("SHELL OIL {n}", 30.0, 85.0, 6),
    ("AMAZON.COM*{n} AMZN.COM/BILL", 12.0, 240.0, 12),
    ("TARGET T-{n}", 20.0, 180.0, 7),
    ("NETFLIX.COM", 15.49, 15.49, 3),
    ("SPOTIFY USA", 11.99, 11.99, 3),
    ("OPENAI *CHATGPT SUBSCR", 20.0, 20.0, 2),
    ("COMCAST XFINITY", 79.99, 79.99, 2),
    ("PG&E ELECTRIC PAYMENT", 60.0, 210.0, 2),
    ("CVS/PHARMACY #{n}", 8.0, 90.0, 5),
    ("EQUINOX FITNESS", 185.0, 185.0, 2),
    ("DELTA AIR LINES {n}", 180.0, 720.0, 2),
    ("MARRIOTT HOTELS", 190.0, 640.0, 2),
    ("BEST BUY #{n}", 60.0, 1400.0, 2),
    ("APPLE STORE R{n}", 99.0, 1899.0, 1),
    ("MONTHLY SERVICE FEE", 12.0, 12.0, 1),
    ("AMC THEATRES #{n}", 14.0, 60.0, 3),
]


async def _ensure_categories() -> None:
    async with session_scope() as session:
        existing = {c.slug for c in (await session.execute(select(Category))).scalars()}
        for slug, name, description in CATEGORIES:
            if slug not in existing:
                session.add(Category(slug=slug, name=name, description=description))
        log.info("categories_seeded", total=len(CATEGORIES))


async def _seed_demo_data(months: int, count: int, seed: int) -> None:
    rng = random.Random(seed)
    async with session_scope() as session:
        users = UserRepository(session)
        user = await users.get_by_email(DEMO_EMAIL)
        if user is not None:
            log.info("demo_user_exists", user_id=user.id, email=DEMO_EMAIL)
            return

        user = await users.create(
            email=DEMO_EMAIL,
            hashed_password=hash_password(DEMO_PASSWORD),
            full_name="Demo User",
        )
        accounts = AccountRepository(session)
        checking = await accounts.create(
            user_id=user.id, name="Everyday Checking", institution="First Demo Bank"
        )
        credit = await accounts.create(
            user_id=user.id, name="Rewards Credit Card", institution="First Demo Bank"
        )

        population = [m for m in MERCHANTS for _ in range(m[3])]
        now = datetime.now(UTC)
        window_start = now - timedelta(days=30 * months)
        balance_delta = {checking.id: Decimal("0"), credit.id: Decimal("0")}

        for i in range(count):
            template, low, high, _ = rng.choice(population)
            description = template.format(n=rng.randint(100, 9999))
            amount = Decimal(str(round(rng.uniform(low, high), 2))) * -1
            occurred_at = window_start + timedelta(
                seconds=rng.randint(0, int((now - window_start).total_seconds()))
            )
            account = credit if rng.random() < 0.55 else checking
            normalized = normalize_description(description)
            result = categorizer.classify(normalized, amount)
            tx = Transaction(
                account_id=account.id,
                external_tx_id=f"seed_{seed}_{i}",
                amount=amount,
                currency="USD",
                description=description,
                normalized_description=normalized,
                merchant=guess_merchant(normalized),
                status=TransactionStatus.CATEGORIZED,
                category_slug=result.category_slug,
                category_source=CategorySource.RULES,
                anomaly_score=0.0,
                occurred_at=occurred_at,
                categorized_at=occurred_at + timedelta(seconds=4),
            )
            session.add(tx)
            balance_delta[account.id] += amount

        # Twice-monthly payroll into checking.
        payroll_dates = [window_start + timedelta(days=d) for d in range(0, 30 * months, 15)]
        for idx, when in enumerate(payroll_dates):
            amount = Decimal("4200.00")
            description = "ACME CORP DIRECT DEPOSIT PAYROLL"
            normalized = normalize_description(description)
            session.add(
                Transaction(
                    account_id=checking.id,
                    external_tx_id=f"seed_{seed}_payroll_{idx}",
                    amount=amount,
                    currency="USD",
                    description=description,
                    normalized_description=normalized,
                    merchant="Acme Corp",
                    status=TransactionStatus.CATEGORIZED,
                    category_slug="income",
                    category_source=CategorySource.RULES,
                    anomaly_score=0.0,
                    occurred_at=when,
                    categorized_at=when + timedelta(seconds=4),
                )
            )
            balance_delta[checking.id] += amount

        # One deliberate outlier so anomaly detection has something to find.
        outlier_desc = "LUXURY WATCH BOUTIQUE"
        session.add(
            Transaction(
                account_id=credit.id,
                external_tx_id=f"seed_{seed}_outlier",
                amount=Decimal("-4820.00"),
                currency="USD",
                description=outlier_desc,
                normalized_description=normalize_description(outlier_desc),
                merchant="Luxury Watch",
                status=TransactionStatus.CATEGORIZED,
                category_slug="shopping",
                category_source=CategorySource.RULES,
                anomaly_score=0.94,
                occurred_at=now - timedelta(days=3),
                categorized_at=now - timedelta(days=3),
            )
        )
        balance_delta[credit.id] += Decimal("-4820.00")

        checking.balance = balance_delta[checking.id]
        credit.balance = balance_delta[credit.id]

        log.info(
            "demo_data_seeded",
            user_id=user.id,
            email=DEMO_EMAIL,
            password=DEMO_PASSWORD,
            accounts=[checking.id, credit.id],
            transactions=count + len(payroll_dates) + 1,
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo data")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--transactions", type=int, default=400)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    configure_logging("seed")
    await _ensure_categories()
    await _seed_demo_data(args.months, args.transactions, args.seed)
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
