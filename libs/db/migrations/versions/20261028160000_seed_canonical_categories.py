"""Initialize the canonical taxonomy without creating a demo workspace.

Revision ID: 20261028160000
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20261028160000"
down_revision = "0004"
branch_labels = None
depends_on = None

# Freeze reference data in the migration so future taxonomy changes do not alter
# historical upgrades. Existing category metadata and financial data stay intact.
CATEGORIES = [
    ("dining_coffee", "Dining & Coffee", "Restaurants, cafes, bars, food delivery"),
    ("groceries", "Groceries", "Supermarkets, grocery and food stores"),
    ("transport", "Transport", "Rideshare, transit, taxi, parking, tolls"),
    ("fuel", "Fuel", "Gas stations and EV charging"),
    ("housing", "Housing", "Rent, mortgage, HOA, property costs"),
    ("utilities", "Utilities", "Electricity, water, gas, internet, phone"),
    ("shopping", "Shopping", "General retail, clothing, household goods"),
    ("electronics", "Electronics", "Computers, phones, gadgets, software hardware"),
    ("entertainment", "Entertainment", "Streaming, games, events, hobbies"),
    ("travel", "Travel", "Flights, hotels, car rental, lodging"),
    ("health", "Health", "Pharmacy, doctors, dental, fitness, insurance"),
    ("education", "Education", "Tuition, courses, books, learning services"),
    ("subscriptions", "Subscriptions", "Recurring digital services and memberships"),
    ("income", "Income", "Salary, deposits, refunds, transfers in"),
    ("transfers", "Transfers", "Movements between the user's own accounts"),
    ("fees", "Fees", "Bank fees, interest charges, service charges"),
    ("other", "Other", "Anything that does not fit another category"),
]


def upgrade() -> None:
    categories = sa.table(
        "categories", sa.column("slug"), sa.column("name"), sa.column("description")
    )
    for slug, name, description in CATEGORIES:
        op.execute(
            categories.insert().from_select(
                ["slug", "name", "description"],
                sa.select(sa.literal(slug), sa.literal(name), sa.literal(description)).where(
                    ~sa.exists().where(categories.c.slug == slug)
                ),
            )
        )


def downgrade() -> None:
    # Reference rows may predate this upgrade or be referenced by transactions.
    # Retain them rather than deleting user metadata or clearing ledger categories.
    pass
