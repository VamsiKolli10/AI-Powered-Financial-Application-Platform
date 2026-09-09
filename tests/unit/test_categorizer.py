from decimal import Decimal

import pytest

from libs.common.text import normalize_description
from libs.db.categories import CATEGORY_SLUGS
from services.transactions.categorizer import anomaly_score, classify


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("STARBUCKS #1234 SEATTLE WA", "dining_coffee"),
        ("DOORDASH*CHIPOTLE", "dining_coffee"),
        ("WHOLE FOODS MKT #10245", "groceries"),
        ("TRADER JOE'S #451", "groceries"),
        ("UBER *TRIP", "transport"),
        ("SHELL OIL 57442136", "fuel"),
        ("NETFLIX.COM", "subscriptions"),
        ("COMCAST XFINITY", "utilities"),
        ("CVS/PHARMACY #8891", "health"),
        ("DELTA AIR LINES 0062", "travel"),
        ("BEST BUY #221", "electronics"),
        ("AMAZON.COM*2H4KD9", "shopping"),
        ("MONTHLY SERVICE FEE", "fees"),
    ],
)
def test_classifies_common_merchants(raw, expected):
    result = classify(normalize_description(raw), Decimal("-20"))
    assert result.category_slug == expected
    assert result.source == "rules"


def test_payroll_inflow_is_income():
    result = classify(normalize_description("ACME CORP DIRECT DEPOSIT PAYROLL"), Decimal("4200"))
    assert result.category_slug == "income"


def test_positive_transfer_is_not_mislabelled_as_income():
    # Regression: the positive-amount fast path once read the transfers keyword list
    # while returning "income".
    result = classify(normalize_description("VENMO CASHOUT"), Decimal("60"))
    assert result.category_slug == "transfers"


def test_positive_payroll_beats_a_merchant_match():
    result = classify(normalize_description("ACME PAYROLL DIRECT DEPOSIT"), Decimal("4200"))
    assert result.category_slug == "income"


def test_unknown_outflow_falls_back_to_other():
    result = classify(normalize_description("ZZQQ UNKNOWN VENDOR"), Decimal("-15"))
    assert result.category_slug == "other"
    assert result.confidence < 0.5


def test_every_rule_maps_to_a_real_category():
    from services.transactions.categorizer import _RULES

    assert {slug for slug, _ in _RULES} <= CATEGORY_SLUGS


def test_substring_does_not_falsely_match():
    # "bp " (fuel) must not fire inside an unrelated word.
    assert classify("bpm music studio", Decimal("-30")).category_slug != "fuel"


class TestAnomalyScore:
    def test_typical_amount_scores_low(self):
        assert (
            anomaly_score(Decimal("-45"), mean_amount=Decimal("50"), max_amount=Decimal("400"))
            < 0.2
        )

    def test_large_multiple_scores_high(self):
        assert (
            anomaly_score(Decimal("-4820"), mean_amount=Decimal("60"), max_amount=Decimal("400"))
            > 0.8
        )

    def test_score_is_monotonic_in_amount(self):
        scores = [
            anomaly_score(Decimal(-x), mean_amount=Decimal("50"), max_amount=Decimal("300"))
            for x in (50, 150, 400, 1200)
        ]
        assert scores == sorted(scores)

    def test_no_history_uses_absolute_scale(self):
        assert (
            anomaly_score(Decimal("-10"), mean_amount=Decimal("0"), max_amount=Decimal("0")) < 0.1
        )

    def test_inflows_are_never_anomalous(self):
        # A large paycheck is not a fraud signal.
        assert (
            anomaly_score(Decimal("4200"), mean_amount=Decimal("60"), max_amount=Decimal("400"))
            == 0.0
        )

    def test_score_is_bounded(self):
        assert (
            0.0
            <= anomaly_score(
                Decimal("-1000000"), mean_amount=Decimal("10"), max_amount=Decimal("50")
            )
            <= 1.0
        )
