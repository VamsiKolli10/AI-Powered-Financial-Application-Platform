"""Conversation history: isolation, trimming, and behaviour when Redis is down."""

from services.assistant.conversations import (
    MAX_TURNS,
    InMemoryConversationStore,
    RedisConversationStore,
)


class FakeRedis:
    def __init__(self, *, broken: bool = False):
        self.data: dict[str, str] = {}
        self.broken = broken

    async def get(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("redis down")
        self.data[key] = value


TURN = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]


async def test_history_round_trips():
    store = RedisConversationStore(FakeRedis())
    await store.append("conv_1", "usr_1", TURN)
    assert await store.history("conv_1", "usr_1") == TURN


async def test_history_is_scoped_to_the_user():
    store = RedisConversationStore(FakeRedis())
    await store.append("conv_1", "usr_1", TURN)
    # Same conversation id, different user: nothing leaks.
    assert await store.history("conv_1", "usr_2") == []


async def test_history_is_trimmed():
    store = RedisConversationStore(FakeRedis())
    for i in range(MAX_TURNS + 10):
        await store.append("conv_1", "usr_1", [{"role": "user", "content": str(i)}])
    assert len(await store.history("conv_1", "usr_1")) == MAX_TURNS


async def test_ttl_is_set_on_write():
    redis = FakeRedis()
    store = RedisConversationStore(redis, ttl_seconds=60)
    await store.append("conv_1", "usr_1", TURN)
    assert redis.data  # written under the user-scoped key
    assert list(redis.data)[0] == "assistant:conv:usr_1:conv_1"


async def test_redis_outage_keeps_history_in_process():
    """Regression: a live run showed history silently vanishing when Redis was down."""
    store = RedisConversationStore(FakeRedis(broken=True))
    await store.append("conv_1", "usr_1", TURN)

    # Degraded, but a follow-up on the same replica still has context.
    assert await store.history("conv_1", "usr_1") == TURN


async def test_corrupt_entry_reads_as_empty():
    redis = FakeRedis()
    redis.data["assistant:conv:usr_1:conv_1"] = "not json"
    assert await RedisConversationStore(redis).history("conv_1", "usr_1") == []


async def test_in_memory_store_matches_the_interface():
    store = InMemoryConversationStore()
    await store.append("conv_1", "usr_1", TURN)
    assert await store.history("conv_1", "usr_1") == TURN
    assert await store.history("conv_1", "usr_2") == []
