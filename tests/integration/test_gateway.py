"""The Gateway, with the real downstream services mounted behind it.

The gateway's HTTP client is pointed at in-process ASGI apps, so a request genuinely
travels client -> gateway -> transactions/insights and back. That is what makes the
identity handoff and the rate limiting worth testing here rather than with mocks.
"""

from contextlib import asynccontextmanager

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from libs.common.config import Settings
from libs.db.session import get_session
from services.gateway.rate_limit import UserRateLimiter, tier_for

REGISTRATION = {
    "email": "vk@example.com",
    "password": "a-strong-password",
    "full_name": "VK",
}


@pytest.fixture
def gateway_settings():
    return Settings(
        environment="test",
        internal_service_token="test-internal-token",
        jwt_secret="gateway-test-secret-of-sufficient-length",
        transactions_url="http://transactions:8000",
        assistant_url="http://assistant:8000",
        insights_url="http://insights:8000",
        notifications_url="http://notifications:8000",
        rate_limit_standard=100,
        rate_limit_llm=20,
        kafka_enabled=False,
        llm_enabled=False,
    )


@pytest.fixture
async def stack(sessionmaker_, session, gateway_settings):
    """Gateway plus the downstream services, all sharing the test database."""
    from services.assistant.main import app as assistant_app
    from services.gateway.main import app as gateway_app
    from services.insights.main import app as insights_app
    from services.notifications.main import app as notifications_app
    from services.transactions.main import app as transactions_app

    async def _override():
        async with sessionmaker_() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    @asynccontextmanager
    async def _scope():
        async with sessionmaker_() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    downstream = {
        "transactions": transactions_app,
        "assistant": assistant_app,
        "insights": insights_app,
        "notifications": notifications_app,
    }
    for service_app in (*downstream.values(), gateway_app):
        service_app.dependency_overrides[get_session] = _override
        service_app.state.session_scope = _scope
        service_app.state.settings = gateway_settings

    transactions_app.state.classifier = None
    transactions_app.state.publisher = None
    insights_app.state.summarizer = None
    assistant_app.state.responder = None

    gateway_app.state.http_client = AsyncClient(
        mounts={
            f"all://{name}": ASGITransport(app=service_app)
            for name, service_app in downstream.items()
        }
    )
    gateway_app.state.rate_limiter = UserRateLimiter(gateway_settings)
    gateway_app.state.status_cache = None

    async with AsyncClient(
        transport=ASGITransport(app=gateway_app), base_url="http://gateway"
    ) as client:
        yield client

    await gateway_app.state.http_client.aclose()
    for service_app in (*downstream.values(), gateway_app):
        service_app.dependency_overrides.clear()


async def _register(client, **overrides) -> str:
    response = await client.post("/api/v1/auth/register", json={**REGISTRATION, **overrides})
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestAuth:
    async def test_register_returns_tokens(self, stack):
        response = await stack.post("/api/v1/auth/register", json=REGISTRATION)

        assert response.status_code == 201
        body = response.json()
        assert body["access_token"] and body["refresh_token"]
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

    async def test_duplicate_email_conflicts(self, stack):
        await _register(stack)
        response = await stack.post("/api/v1/auth/register", json=REGISTRATION)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    async def test_login_round_trip(self, stack):
        await _register(stack)
        response = await stack.post(
            "/api/v1/auth/login",
            json={"email": REGISTRATION["email"], "password": REGISTRATION["password"]},
        )

        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_wrong_password_and_unknown_email_look_identical(self, stack):
        await _register(stack)
        wrong = await stack.post(
            "/api/v1/auth/login",
            json={"email": REGISTRATION["email"], "password": "not-the-password"},
        )
        unknown = await stack.post(
            "/api/v1/auth/login",
            json={"email": "nobody-at-all@example.com", "password": "a-strong-password"},
        )

        # Identical response: never reveal whether an email is registered.
        # (request_id differs per request, so compare everything else.)
        assert wrong.status_code == unknown.status_code == 401

        def without_request_id(body: dict) -> dict:
            return {k: v for k, v in body["error"].items() if k != "request_id"}

        assert without_request_id(wrong.json()) == without_request_id(unknown.json())

    async def test_refresh_issues_a_new_access_token(self, stack):
        registered = (await stack.post("/api/v1/auth/register", json=REGISTRATION)).json()

        response = await stack.post(
            "/api/v1/auth/refresh", json={"refresh_token": registered["refresh_token"]}
        )

        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_access_token_is_not_accepted_as_a_refresh_token(self, stack):
        token = await _register(stack)
        response = await stack.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 401

    async def test_me_returns_the_authenticated_user(self, stack):
        token = await _register(stack)
        response = await stack.get("/api/v1/auth/me", headers=_auth(token))

        assert response.status_code == 200
        assert response.json()["email"] == REGISTRATION["email"]

    async def test_short_password_is_rejected(self, stack):
        response = await stack.post(
            "/api/v1/auth/register", json={**REGISTRATION, "password": "short"}
        )
        assert response.status_code == 422


class TestProxying:
    async def test_full_round_trip_through_the_gateway(self, stack):
        token = await _register(stack)
        account_id = (await stack.get("/api/v1/transactions", headers=_auth(token))).json()
        assert account_id["items"] == []  # a fresh user starts empty

        # The account created at registration is the one we post against.
        me = (await stack.get("/api/v1/auth/me", headers=_auth(token))).json()
        assert me["id"]

    async def test_unauthenticated_requests_are_refused_at_the_edge(self, stack):
        response = await stack.get("/api/v1/transactions")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    async def test_internal_headers_from_a_client_are_not_honoured(self, stack):
        """The internal token is a service credential; presenting it at the edge does nothing."""
        response = await stack.get(
            "/api/v1/transactions",
            headers={
                "X-Internal-Token": "test-internal-token",
                "X-User-Id": "usr_someone_else",
            },
        )

        assert response.status_code == 401

    async def test_spoofed_user_id_is_stripped_before_forwarding(self, stack):
        token = await _register(stack)

        response = await stack.get(
            "/api/v1/transactions",
            headers={**_auth(token), "X-User-Id": "usr_victim"},
        )

        # Served as the token's subject, not the header's claim.
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_unknown_service_is_a_404(self, stack):
        token = await _register(stack)
        response = await stack.get("/api/v1/wallets/all", headers=_auth(token))

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_downstream_outage_becomes_a_502(self, stack):
        from services.gateway.main import app as gateway_app

        token = await _register(stack)

        class DeadTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                raise httpx.ConnectError("connection refused")

        original = gateway_app.state.http_client
        gateway_app.state.http_client = AsyncClient(transport=DeadTransport())
        try:
            response = await stack.get("/api/v1/transactions", headers=_auth(token))
        finally:
            await gateway_app.state.http_client.aclose()
            gateway_app.state.http_client = original

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "UPSTREAM_ERROR"

    async def test_request_id_is_echoed_for_correlation(self, stack):
        token = await _register(stack)
        response = await stack.get(
            "/api/v1/transactions", headers={**_auth(token), "X-Request-ID": "trace-me-123"}
        )

        assert response.headers["X-Request-ID"] == "trace-me-123"


class TestRateLimiting:
    def test_llm_endpoints_are_their_own_tier(self):
        assert tier_for("/assistant/chat") == "llm"
        assert tier_for("/insights/summary") == "llm"
        assert tier_for("/insights/trends") == "standard"
        assert tier_for("/transactions") == "standard"

    async def test_exceeding_the_standard_limit_returns_429(self, stack, gateway_settings):
        from services.gateway.main import app as gateway_app

        token = await _register(stack)
        gateway_app.state.rate_limiter = UserRateLimiter(
            gateway_settings.model_copy(update={"rate_limit_standard": 2})
        )

        first = await stack.get("/api/v1/transactions", headers=_auth(token))
        second = await stack.get("/api/v1/transactions", headers=_auth(token))
        third = await stack.get("/api/v1/transactions", headers=_auth(token))

        assert first.status_code == second.status_code == 200
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "RATE_LIMITED"
        assert int(third.headers["Retry-After"]) >= 1

    async def test_limits_are_per_user(self, stack, gateway_settings):
        from services.gateway.main import app as gateway_app

        first_user = await _register(stack)
        second_user = await _register(stack, email="second@example.com")
        gateway_app.state.rate_limiter = UserRateLimiter(
            gateway_settings.model_copy(update={"rate_limit_standard": 1})
        )

        await stack.get("/api/v1/transactions", headers=_auth(first_user))
        exhausted = await stack.get("/api/v1/transactions", headers=_auth(first_user))
        other = await stack.get("/api/v1/transactions", headers=_auth(second_user))

        assert exhausted.status_code == 429
        assert other.status_code == 200  # one user cannot exhaust another's budget

    async def test_llm_tier_is_limited_separately_from_standard(self, stack, gateway_settings):
        from services.gateway.main import app as gateway_app

        token = await _register(stack)
        gateway_app.state.rate_limiter = UserRateLimiter(
            gateway_settings.model_copy(update={"rate_limit_standard": 10, "rate_limit_llm": 1})
        )

        await stack.get("/api/v1/insights/summary", headers=_auth(token))
        llm_again = await stack.get("/api/v1/insights/summary", headers=_auth(token))
        standard = await stack.get("/api/v1/insights/trends", headers=_auth(token))

        assert llm_again.status_code == 429
        assert standard.status_code == 200  # the cheap tier still has budget


@pytest.fixture
def local_setup(stack, monkeypatch):
    from scripts import seed
    from services.gateway.main import app as gateway_app

    monkeypatch.setattr(seed, "session_scope", gateway_app.state.session_scope)
    return seed


@pytest.fixture
def seed_demo(local_setup):

    async def run():
        await local_setup._ensure_categories()
        await local_setup._seed_demo_data(months=1, count=10, seed=42)

    return run


class TestSeededDemoUser:
    async def test_default_workspace_is_empty_and_can_log_in(self, stack, local_setup):
        await local_setup._ensure_workspace()
        await local_setup._ensure_workspace()
        response = await stack.post(
            "/api/v1/auth/login",
            json={"email": local_setup.DEMO_EMAIL, "password": local_setup.DEMO_PASSWORD},
        )
        assert response.status_code == 200
        transactions = await stack.get(
            "/api/v1/transactions", headers=_auth(response.json()["access_token"])
        )
        assert transactions.json()["items"] == []

    async def test_cleanup_removes_only_generated_rows_and_reverses_their_balance(
        self, stack, session, seed_demo, local_setup
    ):
        from sqlalchemy import select

        from libs.db.models import (
            Account,
            Notification,
            NotificationType,
            Transaction,
            TransactionStatus,
        )
        from libs.db.repositories import UserRepository
        from tests.conftest import within_current_month

        await seed_demo()
        user = await UserRepository(session).get_by_email(local_setup.DEMO_EMAIL)
        account = (
            (await session.execute(select(Account).where(Account.user_id == user.id)))
            .scalars()
            .first()
        )
        real = Transaction(
            account_id=account.id,
            external_tx_id="real-bank-transaction",
            amount=-23,
            currency="USD",
            description="Real purchase",
            normalized_description="real purchase",
            status=TransactionStatus.PENDING_CATEGORIZATION,
            occurred_at=within_current_month(),
        )
        other = Transaction(
            account_id="acct_other",
            external_tx_id="seed_42_outlier",
            amount=-99,
            currency="USD",
            description="Other user's transaction",
            normalized_description="other",
            status=TransactionStatus.PENDING_CATEGORIZATION,
            occurred_at=within_current_month(),
        )
        generated = (
            (await session.execute(select(Transaction).where(Transaction.account_id == account.id)))
            .scalars()
            .first()
        )
        session.add(
            Notification(
                user_id=user.id,
                transaction_id=generated.id,
                type=NotificationType.LARGE_TRANSACTION,
                message="Generated alert",
                dedupe_key="test-generated-alert",
            )
        )
        session.add_all([real, other])
        account.balance -= 23
        await session.commit()

        assert await local_setup._clear_demo_transactions() == 13
        assert await local_setup._clear_demo_transactions() == 0
        await session.refresh(account)
        assert account.balance == -23
        assert await session.get(Transaction, real.id) is not None
        assert await session.get(Transaction, other.id) is not None
        assert (
            await session.execute(
                select(Notification).where(Notification.dedupe_key == "test-generated-alert")
            )
        ).scalar_one_or_none() is None

    async def test_real_seed_creates_a_usable_demo_login(self, stack, seed_demo):
        """A healthy gateway cannot authenticate the demo until setup creates it."""
        from scripts.seed import DEMO_EMAIL, DEMO_PASSWORD

        missing = await stack.post(
            "/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
        )
        assert missing.status_code == 401

        await seed_demo()

        response = await stack.post(
            "/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
        )

        assert response.status_code == 200, response.text
        headers = _auth(response.json()["access_token"])
        transactions = await stack.get("/api/v1/transactions?limit=100", headers=headers)
        assert transactions.status_code == 200
        assert len(transactions.json()["items"]) == 13  # ten purchases, two payrolls, outlier

    async def test_repeated_seed_preserves_existing_password_and_ledger(
        self, stack, session, seed_demo
    ):
        from sqlalchemy import select

        from libs.common.security import hash_password
        from libs.db.models import Account, Transaction
        from libs.db.repositories import UserRepository
        from scripts.seed import DEMO_EMAIL

        await seed_demo()
        user = await UserRepository(session).get_by_email(DEMO_EMAIL)
        changed_password = "changed-demo-password"
        user.hashed_password = hash_password(changed_password)
        await session.commit()

        async def ledger():
            accounts = (
                await session.execute(
                    select(Account.id, Account.balance)
                    .where(Account.user_id == user.id)
                    .order_by(Account.id)
                )
            ).all()
            transactions = (
                await session.execute(
                    select(Transaction.id, Transaction.amount)
                    .join(Account)
                    .where(Account.user_id == user.id)
                    .order_by(Transaction.id)
                )
            ).all()
            return accounts, transactions

        before = await ledger()
        await seed_demo()
        assert await ledger() == before
        response = await stack.post(
            "/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": changed_password}
        )
        assert response.status_code == 200, response.text


class TestPlatformStatus:
    """`/api/v1/status` reports each service's readiness without leaking its details."""

    @staticmethod
    def _readiness(down: set[str], seen: list[str] | None = None) -> AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if seen is not None:
                seen.append(request.url.host)
            assert request.url.path == "/ready"
            if request.url.host in down:
                if request.url.host == "notifications":
                    raise httpx.ConnectError("connection refused")
                return httpx.Response(
                    503,
                    json={"status": "not_ready", "checks": {"database": "error: db-7.internal"}},
                )
            return httpx.Response(200, json={"status": "ready", "checks": {"database": "ok"}})

        return AsyncClient(transport=httpx.MockTransport(handler))

    @staticmethod
    async def _with_client(client: AsyncClient):
        from services.gateway.main import app as gateway_app

        original = gateway_app.state.http_client
        gateway_app.state.http_client = client
        gateway_app.state.status_cache = None
        return gateway_app, original

    async def test_all_services_ready(self, stack):
        token = await _register(stack)
        gateway_app, original = await self._with_client(self._readiness(set()))
        try:
            response = await stack.get("/api/v1/status", headers=_auth(token))
        finally:
            gateway_app.state.http_client = original

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["services"] == {
            "gateway": "ok",
            "transactions": "ok",
            "assistant": "ok",
            "insights": "ok",
            "notifications": "ok",
        }

    async def test_unready_or_unreachable_services_are_reported_without_details(self, stack):
        token = await _register(stack)
        gateway_app, original = await self._with_client(
            self._readiness({"insights", "notifications"})
        )
        try:
            response = await stack.get("/api/v1/status", headers=_auth(token))
        finally:
            gateway_app.state.http_client = original

        body = response.json()
        assert body["status"] == "degraded"
        assert body["services"]["insights"] == "unavailable"  # answered 503
        assert body["services"]["notifications"] == "unavailable"  # unreachable
        assert body["services"]["transactions"] == "ok"
        assert "db-7.internal" not in response.text

    async def test_results_are_cached_briefly(self, stack):
        token = await _register(stack)
        seen: list[str] = []
        gateway_app, original = await self._with_client(self._readiness(set(), seen))
        try:
            await stack.get("/api/v1/status", headers=_auth(token))
            await stack.get("/api/v1/status", headers=_auth(token))
        finally:
            gateway_app.state.http_client = original

        assert len(seen) == 4  # one probe per downstream service, not eight

    async def test_status_requires_a_user_token(self, stack):
        response = await stack.get("/api/v1/status")
        assert response.status_code == 401

    async def test_status_is_not_mistaken_for_a_service(self, stack):
        token = await _register(stack)
        gateway_app, original = await self._with_client(self._readiness(set()))
        try:
            response = await stack.get("/api/v1/status", headers=_auth(token))
        finally:
            gateway_app.state.http_client = original
        assert response.json().get("error", {}).get("code") != "NOT_FOUND"
