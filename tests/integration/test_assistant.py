"""Assistant end to end: grounded answers, refusals, history, isolation."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from libs.db.models import CategorySource, Transaction, TransactionStatus
from libs.llm_client.assistant import AssistantResponder
from libs.llm_client.client import LLMClient
from libs.llm_client.rate_limit import InMemoryRateLimiter
from tests.conftest import OTHER_USER_ID

NOW = datetime.now(UTC)
LAST_MONTH = (NOW.replace(day=1) - timedelta(days=5)).replace(day=10)


class ScriptedCompleter:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0
        self.last_messages = None

    async def complete(self, *, messages, model, timeout, max_tokens):
        self.calls += 1
        self.last_messages = messages
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _responder(reply):
    return AssistantResponder(
        LLMClient(ScriptedCompleter(reply), max_attempts=1),
        rate_limiter=InMemoryRateLimiter(per_minute=600),
    )


def _tx(session, account_id, external_id, amount, category, when, description="MERCHANT X"):
    session.add(
        Transaction(
            account_id=account_id,
            external_tx_id=external_id,
            amount=Decimal(str(amount)),
            currency="USD",
            description=description,
            normalized_description=description.lower(),
            merchant=description.title(),
            status=TransactionStatus.CATEGORIZED,
            category_slug=category,
            category_source=CategorySource.RULES,
            occurred_at=when,
        )
    )


@pytest.fixture
async def spending(session):
    """Dining spend last month, groceries this month, and one big outlier."""
    for i in range(3):
        _tx(session, "acct_test", f"a_{i}", -104.10, "dining_coffee", LAST_MONTH, "CAFE ROMA")
    _tx(session, "acct_test", "b_1", -150.00, "groceries", NOW - timedelta(days=2), "WHOLE FOODS")
    _tx(session, "acct_test", "b_2", -820.00, "electronics", NOW - timedelta(days=3), "BEST BUY")
    # Another user's data, which must never appear in an answer.
    _tx(session, "acct_other", "c_1", -9999.00, "travel", NOW - timedelta(days=1), "SECRET TRIP")
    await session.commit()


@pytest.fixture
async def client(app_assistant, make_client):
    return await make_client(app_assistant)


@pytest.fixture
def app_assistant():
    from services.assistant.conversations import InMemoryConversationStore
    from services.assistant.main import app

    app.state.responder = None
    app.state.conversations = InMemoryConversationStore()
    yield app
    app.state.responder = None
    app.state.conversations = None


async def test_category_question_is_answered_from_the_ledger(spending, client):
    response = await client.post(
        "/assistant/chat", json={"message": "How much did I spend on dining last month?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert "$312.30" in body["reply"]
    assert "3 transactions" in body["reply"]
    assert body["sources"][0]["category"] == "Dining & Coffee"
    assert body["conversation_id"].startswith("conv_")


async def test_answer_without_an_llm_is_still_correct(spending, client):
    body = (
        await client.post("/assistant/chat", json={"message": "What did I spend this month?"})
    ).json()

    assert body["reply_source"] == "draft"
    assert "$970.00" in body["reply"]  # 150 + 820


async def test_largest_transaction_question(spending, client):
    body = (
        await client.post(
            "/assistant/chat", json={"message": "What was my largest transaction this month?"}
        )
    ).json()

    assert "$820.00" in body["reply"]
    assert "Best Buy" in body["reply"]


async def test_question_about_a_period_with_no_data(client):
    body = (
        await client.post("/assistant/chat", json={"message": "What did I spend last month?"})
    ).json()

    assert "don't see any transactions" in body["reply"]


async def test_write_request_is_refused_with_400(client):
    response = await client.post(
        "/assistant/chat", json={"message": "Move $50 to my savings account"}
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "READ_ONLY_ASSISTANT"
    assert "cannot move money" in error["message"]


async def test_delete_request_is_refused_and_points_at_the_api(client):
    response = await client.post("/assistant/chat", json={"message": "Delete my last transaction"})

    assert response.status_code == 400
    assert "PATCH /api/v1/transactions" in response.json()["error"]["message"]


async def test_another_users_spending_never_appears(spending, client):
    body = (
        await client.post("/assistant/chat", json={"message": "What did I spend this month?"})
    ).json()

    assert "9,999" not in body["reply"]
    assert "Travel" not in body["reply"]


async def test_llm_rewording_keeps_the_retrieved_figures(spending, app_assistant, client):
    app_assistant.state.responder = _responder(
        json.dumps({"reply": "You spent $312.30 on Dining & Coffee last month."})
    )

    body = (
        await client.post(
            "/assistant/chat", json={"message": "How much did I spend on dining last month?"}
        )
    ).json()

    assert body["reply_source"] == "llm"
    assert "$312.30" in body["reply"]


async def test_prompt_carries_computed_facts_and_a_correct_draft(spending, app_assistant, client):
    responder = _responder(json.dumps({"reply": "ok"}))
    app_assistant.state.responder = responder

    await client.post(
        "/assistant/chat", json={"message": "How much did I spend on dining last month?"}
    )

    sent = responder.client.completer.last_messages[-1]["content"]
    assert "FACTS:" in sent and "DRAFT:" in sent
    assert "312.3" in sent  # the model is given the number, it does not compute one


async def test_provider_failure_falls_back_to_the_draft(spending, app_assistant, client):
    app_assistant.state.responder = _responder(TimeoutError("provider down"))

    body = (
        await client.post(
            "/assistant/chat", json={"message": "How much did I spend on dining last month?"}
        )
    ).json()

    assert body["reply_source"] == "draft"
    assert "$312.30" in body["reply"]  # accuracy never degrades


async def test_conversation_history_is_kept_and_readable(spending, client):
    first = (
        await client.post("/assistant/chat", json={"message": "What did I spend this month?"})
    ).json()
    conversation_id = first["conversation_id"]

    await client.post(
        "/assistant/chat",
        json={"message": "And on groceries?", "conversation_id": conversation_id},
    )

    body = (await client.get(f"/assistant/conversations/{conversation_id}")).json()
    assert len(body["turns"]) == 4  # two questions, two replies
    assert [t["role"] for t in body["turns"]] == ["user", "assistant", "user", "assistant"]


async def test_history_is_passed_to_the_model_on_a_follow_up(spending, app_assistant, client):
    responder = _responder(json.dumps({"reply": "ok"}))
    app_assistant.state.responder = responder

    first = (
        await client.post("/assistant/chat", json={"message": "What did I spend this month?"})
    ).json()
    await client.post(
        "/assistant/chat",
        json={"message": "And on groceries?", "conversation_id": first["conversation_id"]},
    )

    roles = [m["role"] for m in responder.client.completer.last_messages]
    assert roles.count("user") > 1  # the earlier turn is in the prompt


async def test_a_conversation_is_not_readable_by_another_user(spending, client):
    created = (
        await client.post("/assistant/chat", json={"message": "What did I spend this month?"})
    ).json()

    body = (
        await client.get(
            f"/assistant/conversations/{created['conversation_id']}",
            headers={"X-User-Id": OTHER_USER_ID},
        )
    ).json()

    assert body["turns"] == []


async def test_unauthenticated_requests_are_rejected(client):
    response = await client.post(
        "/assistant/chat",
        json={"message": "What did I spend?"},
        headers={"X-Internal-Token": "", "X-User-Id": ""},
    )
    assert response.status_code == 401
