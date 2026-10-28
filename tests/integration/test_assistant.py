"""Assistant end to end: grounded answers, refusals, history, isolation."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from libs.db.models import CategorySource, Transaction, TransactionStatus
from libs.llm_client.assistant import AssistantResponder
from libs.llm_client.client import LLMClient
from libs.llm_client.rate_limit import InMemoryRateLimiter
from tests.conftest import OTHER_USER_ID, within_current_month

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
        if self.reply == "__draft__":
            draft = messages[-1]["content"].split("DRAFT: ", 1)[1]
            return json.dumps({"reply": draft.replace("You spent", "Your spending was")})
        return self.reply


def _responder(reply):
    return AssistantResponder(
        LLMClient(ScriptedCompleter(reply), max_attempts=1),
        rate_limiter=InMemoryRateLimiter(per_minute=600),
    )


def _tx(
    session,
    account_id,
    external_id,
    amount,
    category,
    when,
    description="MERCHANT X",
    currency="USD",
):
    session.add(
        Transaction(
            account_id=account_id,
            external_tx_id=external_id,
            amount=Decimal(str(amount)),
            currency=currency,
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
    _tx(session, "acct_test", "b_1", -150.00, "groceries", within_current_month(0.8), "WHOLE FOODS")
    _tx(session, "acct_test", "b_2", -820.00, "electronics", within_current_month(0.7), "BEST BUY")
    # Another user's data, which must never appear in an answer.
    _tx(session, "acct_other", "c_1", -9999.00, "travel", within_current_month(0.9), "SECRET TRIP")
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


@pytest.mark.parametrize(
    "message",
    [
        "Delete my last transaction",
        "Delete my transactions",
        "Remove those records",
        "Erase my payments",
    ],
)
async def test_delete_request_is_refused_and_points_at_the_api(client, app_assistant, message):
    responder = _responder(json.dumps({"reply": "This must not reach the provider."}))
    app_assistant.state.responder = responder
    response = await client.post("/assistant/chat", json={"message": message})

    assert response.status_code == 400
    assert "PATCH /api/v1/transactions" in response.json()["error"]["message"]
    assert responder.client.completer.calls == 0


async def test_another_users_spending_never_appears(spending, client):
    body = (
        await client.post("/assistant/chat", json={"message": "What did I spend this month?"})
    ).json()

    assert "9,999" not in body["reply"]
    assert "Travel" not in body["reply"]


async def test_llm_rewording_keeps_the_retrieved_figures(spending, app_assistant, client):
    app_assistant.state.responder = _responder("__draft__")

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
    assert body["fallback_reason"] == "provider_unavailable"
    assert "$312.30" in body["reply"]  # accuracy never degrades


async def test_greeting_explains_capabilities_instead_of_unrelated_totals(spending, client):
    body = (
        await client.post("/assistant/chat", json={"message": "Hello, what can you help me with?"})
    ).json()
    assert "I can summarize your spending" in body["reply"]
    assert "$970" not in body["reply"]


async def test_follow_up_keeps_period_but_uses_its_new_category(spending, client):
    first = (
        await client.post(
            "/assistant/chat", json={"message": "What did I spend on groceries last month?"}
        )
    ).json()
    body = (
        await client.post(
            "/assistant/chat",
            json={"message": "And on dining?", "conversation_id": first["conversation_id"]},
        )
    ).json()
    assert "$312.30" in body["reply"]
    assert body["sources"][0]["category"] == "Dining & Coffee"
    explicit = (
        await client.post(
            "/assistant/chat",
            json={
                "message": "And groceries this month?",
                "conversation_id": first["conversation_id"],
            },
        )
    ).json()
    assert "$150.00" in explicit["reply"]


async def test_status_discloses_disabled_and_missing_provider(client, app_assistant, monkeypatch):
    from libs.common.config import Settings

    monkeypatch.setattr(app_assistant.state, "settings", Settings(llm_enabled=False), raising=False)
    status = (await client.get("/assistant/status")).json()
    assert status["mode"] == "disabled"
    monkeypatch.setattr(
        app_assistant.state, "settings", Settings(llm_enabled=True, openai_api_key="sk-replace-me")
    )
    status = (await client.get("/assistant/status")).json()
    assert status["mode"] == "unconfigured"
    assert "sk-replace-me" not in json.dumps(status)


async def test_slow_provider_returns_a_grounded_answer_before_deadline(
    spending, client, app_assistant
):
    class SlowCompleter:
        async def complete(self, **kwargs):
            await asyncio.sleep(2)
            return '{"reply":"late"}'

    app_assistant.state.responder = AssistantResponder(
        LLMClient(SlowCompleter(), timeout_seconds=0.03),
        rate_limiter=InMemoryRateLimiter(per_minute=600),
    )
    async with asyncio.timeout(0.5):
        response = await client.post(
            "/assistant/chat", json={"message": "What did I spend this month?"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_reason"] == "provider_timeout"
    assert body["reply_source"] == "draft" and "$970.00" in body["reply"]


@pytest.mark.parametrize("reply", [None, 123, {"text": "not a reply"}])
async def test_invalid_model_reply_is_disclosed(spending, client, app_assistant, reply):
    app_assistant.state.responder = _responder(json.dumps({"reply": reply}))
    body = (
        await client.post("/assistant/chat", json={"message": "What did I spend this month?"})
    ).json()
    assert body["fallback_reason"] == "invalid_response"
    assert body["reply_source"] == "draft"


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


async def test_answers_stay_in_one_currency(spending, session, client):
    _tx(
        session,
        "acct_test",
        "eur_1",
        -700.00,
        "travel",
        within_current_month(0.6),
        "TAP AIR",
        currency="EUR",
    )
    await session.commit()

    body = (
        await client.post("/assistant/chat", json={"message": "What did I spend this month?"})
    ).json()

    # 150 + 820 USD; the EUR flight is neither added in nor shown with a dollar sign.
    assert "$970.00" in body["reply"]
    assert "700" not in body["reply"]
    assert "USD only" in body["reply"]
    assert "EUR is not included" in body["reply"]


async def test_foreign_only_spending_is_disclosed_and_can_be_requested(session, client):
    _tx(
        session, "acct_test", "foreign_only", -500, "travel", within_current_month(), currency="EUR"
    )
    await session.commit()
    response = await client.post(
        "/assistant/chat", json={"message": "What did I spend this month?"}
    )
    assert "USD spending" in response.json()["reply"]
    assert "EUR is not included" in response.json()["reply"]
    response = await client.post(
        "/assistant/chat", json={"message": "What did I spend this month?", "currency": "eur"}
    )
    assert "€500.00" in response.json()["reply"]


async def test_largest_query_searches_beyond_latest_page_and_filters_currency_first(
    session, client
):
    _tx(
        session,
        "acct_test",
        "older_largest",
        -1000,
        "groceries",
        within_current_month(0.1),
        "OLD BIG",
    )
    for i in range(110):
        _tx(
            session,
            "acct_test",
            f"later_{i}",
            -1,
            "groceries",
            within_current_month(0.8),
            "EUR SMALL",
            currency="EUR",
        )
    # Larger records outside user/currency/window must not become candidates.
    _tx(
        session,
        "acct_other",
        "other_user_big",
        -5000,
        "groceries",
        within_current_month(),
        "SECRET",
    )
    await session.commit()
    response = await client.post(
        "/assistant/chat", json={"message": "What was my largest groceries transaction this month?"}
    )
    reply = response.json()["reply"]
    assert "$1,000.00" in reply and "Old Big" in reply
    assert "largest transaction" in reply
    assert "SECRET" not in reply


async def test_invalid_assistant_currency_is_rejected(client):
    response = await client.post(
        "/assistant/chat", json={"message": "What did I spend?", "currency": "dollars"}
    )
    assert response.status_code == 422


async def test_llm_reply_retains_deterministic_currency_disclosure(session, app_assistant, client):
    _tx(session, "acct_test", "foreign_llm", -500, "travel", within_current_month(), currency="EUR")
    await session.commit()
    app_assistant.state.responder = _responder("__draft__")
    body = (await client.post("/assistant/chat", json={"message": "What did I spend?"})).json()
    assert body["reply_source"] == "llm"
    assert "USD only" in body["reply"] and "EUR is not included" in body["reply"]
