"""Canonical category taxonomy. The LLM must classify into exactly these slugs."""

CATEGORIES: list[tuple[str, str, str]] = [
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

CATEGORY_SLUGS: set[str] = {slug for slug, _, _ in CATEGORIES}
CATEGORY_NAMES: dict[str, str] = {slug: name for slug, name, _ in CATEGORIES}
FALLBACK_CATEGORY = "other"
