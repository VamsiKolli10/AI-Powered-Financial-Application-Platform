"""Human-readable money for messages and templated sentences.

Display only: amounts are always stored and computed as Decimal. Clients that render
currency themselves (the dashboard uses Intl.NumberFormat) receive the raw amount and
its ISO code instead.
"""

from __future__ import annotations

from decimal import Decimal

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "INR": "₹", "CAD": "CA$", "AUD": "A$"}


def format_money(amount: Decimal | float, currency: str = "USD") -> str:
    """`$1,234.50`, `€12.00`, or `CHF 99.00` for currencies without a symbol here."""
    value = abs(Decimal(str(amount)))
    code = currency.upper()
    symbol = _SYMBOLS.get(code)
    return f"{symbol}{value:,.2f}" if symbol else f"{code} {value:,.2f}"
