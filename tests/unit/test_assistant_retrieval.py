"""Question parsing: periods and categories."""

from datetime import UTC, datetime

import pytest

from services.assistant.retrieval import parse_category, parse_period, plan

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("message", "expected_label"),
    [
        ("How much did I spend last month?", "August 2026"),
        ("What did I spend this month?", "September 2026"),
        ("How much in August?", "August 2026"),
        ("What about in December?", "December 2025"),  # nearest past December
        ("Spending over the last 3 months", "the last 3 months"),
        ("What did I spend this year?", "2026"),
        ("How much last week?", "last week"),
    ],
)
def test_period_parsing(message, expected_label):
    assert parse_period(message, now=NOW).label == expected_label


def test_unrecognised_period_defaults_to_this_month():
    assert parse_period("How much am I spending?", now=NOW).label == "September 2026"


def test_last_month_window_is_the_whole_previous_month():
    period = parse_period("last month", now=NOW)
    assert period.start == datetime(2026, 8, 1, tzinfo=UTC)
    assert period.end == datetime(2026, 9, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("How much on dining?", "dining_coffee"),
        ("spending on restaurants", "dining_coffee"),
        ("my coffee habit", "dining_coffee"),
        ("groceries last month", "groceries"),
        ("how much on gas", "fuel"),
        ("streaming subscriptions", "subscriptions"),
        ("what did I pay in rent", "housing"),
        ("my paycheck", "income"),
    ],
)
def test_category_parsing(message, expected):
    assert parse_category(message) == expected


def test_no_category_mentioned():
    assert parse_category("How much did I spend last month?") is None


def test_longer_hint_wins_over_a_shorter_one():
    assert parse_category("how much on food delivery") == "dining_coffee"


def test_plan_detects_intent():
    assert plan("what were my largest transactions?", now=NOW).wants_largest is True
    assert plan("give me a breakdown by categories", now=NOW).wants_breakdown is True
