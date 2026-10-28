from decimal import Decimal

from libs.common.money import format_money


def test_known_symbols():
    assert format_money(Decimal("1234.5"), "USD") == "$1,234.50"
    assert format_money(Decimal("12"), "eur") == "€12.00"
    assert format_money(9.99, "GBP") == "£9.99"


def test_unknown_currency_uses_its_code():
    assert format_money(Decimal("99"), "CHF") == "CHF 99.00"


def test_sign_is_dropped_for_display():
    # Callers say "spent" or "received"; the sign is the sentence's job.
    assert format_money(Decimal("-842.00"), "USD") == "$842.00"
