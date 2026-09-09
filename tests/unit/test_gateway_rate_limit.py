"""Edge rate limiting, including what happens when Redis is unreachable."""

import pytest

from libs.common.config import Settings
from libs.common.errors import RateLimitedError
from services.gateway.rate_limit import UserRateLimiter, tier_for

SETTINGS = Settings(environment="test", rate_limit_standard=3, rate_limit_llm=1)


class BrokenRedis:
    async def eval(self, *args, **kwargs):
        raise ConnectionError("redis down")


class WorkingRedis:
    """Minimal stand-in: enough of EVAL for the token-bucket script's contract."""

    def __init__(self):
        self.calls = 0

    async def eval(self, script, numkeys, key, capacity, refill, now, requested):
        self.calls += 1
        return [1 if self.calls <= capacity else 0, 30]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/assistant/chat", "llm"),
        ("/insights/summary", "llm"),
        ("/insights/trends", "standard"),
        ("/transactions/tx_1", "standard"),
        ("/notifications", "standard"),
    ],
)
def test_tiering(path, expected):
    assert tier_for(path) == expected


async def test_requests_within_the_limit_pass():
    limiter = UserRateLimiter(SETTINGS)
    for _ in range(3):
        await limiter.check(user_id="usr_1", path="/transactions")


async def test_exceeding_the_limit_raises_with_retry_after():
    limiter = UserRateLimiter(SETTINGS)
    for _ in range(3):
        await limiter.check(user_id="usr_1", path="/transactions")

    with pytest.raises(RateLimitedError) as excinfo:
        await limiter.check(user_id="usr_1", path="/transactions")
    assert excinfo.value.retry_after >= 1


async def test_tiers_have_separate_budgets():
    limiter = UserRateLimiter(SETTINGS)
    await limiter.check(user_id="usr_1", path="/assistant/chat")
    with pytest.raises(RateLimitedError):
        await limiter.check(user_id="usr_1", path="/assistant/chat")

    # The standard tier is untouched by exhausting the LLM tier.
    await limiter.check(user_id="usr_1", path="/transactions")


async def test_users_do_not_share_a_budget():
    limiter = UserRateLimiter(SETTINGS)
    for _ in range(3):
        await limiter.check(user_id="usr_1", path="/transactions")
    with pytest.raises(RateLimitedError):
        await limiter.check(user_id="usr_1", path="/transactions")

    await limiter.check(user_id="usr_2", path="/transactions")


async def test_redis_outage_degrades_to_a_local_bucket_rather_than_no_limit():
    """Regression: a load run showed 300 requests admitted against a 100/min limit
    because the shared limiter failed open when Redis was unreachable."""
    limiter = UserRateLimiter(SETTINGS, redis_client=BrokenRedis())

    for _ in range(3):
        await limiter.check(user_id="usr_1", path="/transactions")

    with pytest.raises(RateLimitedError):
        await limiter.check(user_id="usr_1", path="/transactions")


async def test_shared_bucket_is_used_when_redis_works():
    redis = WorkingRedis()
    limiter = UserRateLimiter(SETTINGS, redis_client=redis)
    await limiter.check(user_id="usr_1", path="/transactions")
    assert redis.calls == 1
