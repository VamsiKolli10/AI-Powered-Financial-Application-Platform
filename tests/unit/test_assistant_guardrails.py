"""The read-only gate. These are the tests that matter most in this service."""

import pytest

from services.assistant.guardrails import check


@pytest.mark.parametrize(
    "message",
    [
        "How much did I spend on dining last month?",
        "What were my biggest transactions in August?",
        "Show me a breakdown of my spending this year",
        "Why is my grocery spending higher than usual?",
        "Compare my dining spending to last month",
        "How much did I transfer to savings last month?",  # asks about history
        "Did I pay my phone bill in July?",
        "What did I spend at restaurants?",
    ],
)
def test_questions_are_allowed(message):
    assert check(message).allowed is True


@pytest.mark.parametrize(
    "message",
    [
        "Move $50 to my savings account",
        "Transfer 200 dollars to checking",
        "Please send $20 to my sister",
        "Delete my last transaction",
        "Remove that charge from my history",
        "Can you move $50 to savings?",
        "Change the category of that transaction to groceries",
        "Update the amount on transaction tx_123",
        "Pay my credit card bill",
        "Close my account",
        "Mark all my notifications as read",
    ],
)
def test_write_intent_is_refused(message):
    verdict = check(message)
    assert verdict.allowed is False
    assert verdict.reason


def test_refusal_points_at_the_right_endpoint():
    assert "PATCH /api/v1/transactions" in check("Change that transaction's category").reason
    assert "PATCH /api/v1/notifications" in check("Mark it as read").reason


def test_money_movement_refusal_does_not_offer_an_alternative_endpoint():
    # There is no money-movement endpoint to point at; the platform does not do it.
    reason = check("Move $50 to my savings account").reason
    assert "out of scope" in reason
    assert "PATCH" not in reason


def test_empty_message_is_refused():
    assert check("   ").allowed is False
