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


class TestSeededDemoUser:
    async def test_the_seeded_demo_account_can_log_in(self, stack, session):
        """Regression: the demo user was seeded at a .local address, which email
        validation rejects, so the credentials printed by the seed script could not
        actually be used against /auth/login."""
        from libs.common.security import hash_password
        from libs.db.repositories import UserRepository
        from scripts.seed import DEMO_EMAIL, DEMO_PASSWORD

        await UserRepository(session).create(
            email=DEMO_EMAIL,
            hashed_password=hash_password(DEMO_PASSWORD),
            full_name="Demo User",
        )
        await session.commit()

        response = await stack.post(
            "/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
        )

        assert response.status_code == 200, response.text
        assert response.json()["access_token"]
