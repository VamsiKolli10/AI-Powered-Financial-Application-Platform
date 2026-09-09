import pytest

from libs.common.text import cache_key, guess_merchant, normalize_description


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("STARBUCKS #1234 SEATTLE WA", "starbucks"),
        ("SQ *TACO STAND", "taco stand"),
        ("AMAZON.COM*2H4KD9 AMZN.COM/BILL", "amazon com amzn com bill"),
        ("POS DEBIT CARD XXXX4412 TRADER JOE'S #451", "trader joe's"),
        ("UBER *TRIP 08/14", "uber trip"),
    ],
)
def test_normalize_strips_noise_and_identifiers(raw, expected):
    assert normalize_description(raw) == expected


def test_normalize_never_returns_empty():
    assert normalize_description("#### 12345678") != ""


def test_normalize_removes_long_digit_runs():
    # Card fragments and account numbers must never reach the LLM provider.
    assert "4412991200" not in normalize_description("PAYMENT 4412991200 ACME")


def test_city_and_state_tail_are_dropped():
    assert normalize_description("CHIPOTLE 2841 SAN JOSE CA") == "chipotle"


def test_merchant_key_keeps_multi_word_brands():
    from libs.common.text import merchant_key

    assert merchant_key(normalize_description("WHOLE FOODS MKT #10245")) == "whole foods"


def test_same_merchant_shares_a_cache_key():
    a = cache_key(normalize_description("STARBUCKS #1234 SEATTLE WA"))
    b = cache_key(normalize_description("STARBUCKS #9876 PORTLAND OR"))
    assert a == b


def test_guess_merchant():
    assert guess_merchant("whole foods mkt") == "Whole Foods Mkt"
    assert guess_merchant("") is None
