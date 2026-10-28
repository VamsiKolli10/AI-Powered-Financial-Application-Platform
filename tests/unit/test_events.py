"""Event contract and consumer-loop behaviour."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from aiokafka import TopicPartition

from libs.events import (
    InMemoryEventPublisher,
    NullEventPublisher,
    TransactionAnomalyFlagged,
    TransactionCategorized,
    parse_event,
)
from libs.events.consumer import KafkaEventConsumer
from libs.events.schemas import SCHEMA_VERSION


def _categorized(**overrides):
    base = {
        "transaction_id": "tx_1",
        "account_id": "acct_1",
        "user_id": "usr_1",
        "category": "dining_coffee",
        "category_source": "llm",
        "amount": Decimal("-42.50"),
        "merchant": "Starbucks",
        "anomaly_score": 0.02,
        "occurred_at": datetime(2026, 9, 5, 14, 30, tzinfo=UTC),
    }
    return TransactionCategorized(**{**base, **overrides})


def test_event_carries_type_and_schema_version():
    event = _categorized()
    assert event.event_type == "transaction.categorized"
    assert event.schema_version == SCHEMA_VERSION
    assert event.published_at.tzinfo is not None


def test_round_trips_through_json():
    event = _categorized()
    decoded = parse_event(json.loads(event.model_dump_json()))
    assert decoded == event
    assert isinstance(decoded, TransactionCategorized)


def test_unknown_event_type_is_skipped_not_an_error():
    # A newer producer during a rolling deploy must not poison this consumer.
    assert parse_event({"event_type": "transaction.teleported"}) is None


def test_unknown_fields_are_ignored():
    payload = json.loads(_categorized().model_dump_json())
    payload["field_from_a_future_version"] = "surprise"
    assert parse_event(payload) is not None


def test_events_for_one_account_share_a_partition_key():
    a = _categorized(transaction_id="tx_1")
    b = _categorized(transaction_id="tx_2")
    assert a.partition_key == b.partition_key == "acct_1"


def test_anomaly_event_requires_a_reason():
    event = TransactionAnomalyFlagged(
        transaction_id="tx_1",
        account_id="acct_1",
        user_id="usr_1",
        amount=Decimal("-4820"),
        anomaly_score=0.94,
        reason="amount_outlier",
    )
    assert parse_event(json.loads(event.model_dump_json())) == event


class TestPublishers:
    async def test_null_publisher_swallows_events(self):
        await NullEventPublisher().publish(_categorized())  # must not raise

    async def test_in_memory_publisher_records_and_fans_out(self):
        seen = []

        async def handler(event):
            seen.append(event)

        publisher = InMemoryEventPublisher(handlers=[handler])
        await publisher.publish(_categorized())

        assert len(publisher.of_type("transaction.categorized")) == 1
        assert len(seen) == 1


class TestConsumerLoop:
    """The commit rules are the contract: commit on success, hold the offset on failure."""

    class FakeMessage:
        def __init__(self, value, offset=0, topic="transactions.events", partition=0):
            self.value = value
            self.offset = offset
            self.topic = topic
            self.partition = partition

    def _consumer(self, handler):
        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)
        consumer.commits = 0

        async def _commit(message, **kwargs):
            consumer.commits += 1

        consumer._commit = _commit
        return consumer

    async def test_successful_handler_commits(self):
        handled = []

        async def handler(event):
            handled.append(event)

        consumer = self._consumer(handler)
        await consumer._handle_message(self.FakeMessage(_categorized().model_dump_json()))

        assert len(handled) == 1
        assert consumer.commits == 1

    async def test_failing_handler_does_not_commit(self):
        async def handler(event):
            raise RuntimeError("database down")

        consumer = self._consumer(handler)
        await consumer._handle_message(self.FakeMessage(_categorized().model_dump_json()))

        # The loop must retry this record rather than advancing to another one.
        assert consumer.commits == 0

    async def test_undecodable_message_is_committed_and_skipped(self):
        async def handler(event):  # pragma: no cover - must not be called
            raise AssertionError("handler should not run")

        consumer = self._consumer(handler)
        await consumer._handle_message(self.FakeMessage("{not json"))

        assert consumer.commits == 1

    @pytest.mark.parametrize(
        "payload",
        [None, [], "text", 42, {"event_type": "transaction.categorized", "amount": "invalid"}],
    )
    async def test_invalid_payload_is_skipped_without_calling_handler(self, payload):
        handler = AsyncMock()
        consumer = self._consumer(handler)

        assert await consumer._handle_message(self.FakeMessage(json.dumps(payload))) is True
        handler.assert_not_awaited()
        assert consumer.commits == 1

    async def test_commit_acknowledges_only_processed_partition_and_offset(self):
        consumer = KafkaEventConsumer(
            bootstrap_servers="unused", group_id="test", handler=AsyncMock()
        )
        consumer._consumer = AsyncMock()
        message = self.FakeMessage(_categorized().model_dump_json(), offset=7, partition=2)

        await consumer._handle_message(message)

        consumer._consumer.commit.assert_awaited_once_with(
            {TopicPartition("transactions.events", 2): 8}
        )

    class FakeKafka:
        def __init__(self, messages):
            self.messages = iter(messages)
            self.commits = []
            self.assigned = {TopicPartition("transactions.events", 0)}

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                item = next(self.messages)
            except StopIteration:
                raise StopAsyncIteration from None
            if isinstance(item, Exception):
                raise item
            return item

        def assignment(self):
            return self.assigned

        async def commit(self, offsets):
            self.commits.append(offsets)

    async def test_failed_handler_retries_before_later_message_is_committed(self, monkeypatch):
        monkeypatch.setattr("libs.events.consumer.RETRY_DELAY_SECONDS", 0)
        seen = []

        async def handler(event):
            seen.append(event.transaction_id)
            if len(seen) == 1:
                raise RuntimeError("database temporarily unavailable")

        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)
        broker = self.FakeKafka(
            [
                self.FakeMessage(_categorized(transaction_id="tx_1").model_dump_json(), offset=0),
                self.FakeMessage(_categorized(transaction_id="tx_2").model_dump_json(), offset=1),
            ]
        )
        consumer._consumer = broker

        await consumer._run()

        assert seen == ["tx_1", "tx_1", "tx_2"]
        assert broker.commits == [
            {TopicPartition("transactions.events", 0): 1},
            {TopicPartition("transactions.events", 0): 2},
        ]

    async def test_fetch_failure_does_not_terminate_loop(self, monkeypatch):
        monkeypatch.setattr("libs.events.consumer.RETRY_DELAY_SECONDS", 0)
        handler = AsyncMock()
        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)
        broker = self.FakeKafka(
            [
                RuntimeError("temporary fetch error"),
                self.FakeMessage(_categorized().model_dump_json()),
            ]
        )
        consumer._consumer = broker

        await consumer._run()

        handler.assert_awaited_once()
        assert len(broker.commits) == 1

    async def test_malformed_records_do_not_block_next_valid_event(self):
        handler = AsyncMock()
        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)
        broker = self.FakeKafka(
            [
                self.FakeMessage("{bad json", offset=0),
                self.FakeMessage("null", offset=1),
                self.FakeMessage('{"event_type":"transaction.categorized"}', offset=2),
                self.FakeMessage(_categorized().model_dump_json(), offset=3),
            ]
        )
        consumer._consumer = broker

        await consumer._run()

        handler.assert_awaited_once()
        assert broker.commits == [
            {TopicPartition("transactions.events", 0): offset} for offset in range(1, 5)
        ]

    async def test_commit_failure_retries_record_without_advancing(self, monkeypatch):
        monkeypatch.setattr("libs.events.consumer.RETRY_DELAY_SECONDS", 0)
        handler = AsyncMock()
        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)
        broker = self.FakeKafka([self.FakeMessage(_categorized().model_dump_json())])
        broker.commit = AsyncMock(side_effect=[RuntimeError("broker unavailable"), None])
        consumer._consumer = broker

        await consumer._run()

        assert handler.await_count == 2
        assert broker.commit.await_count == 2

    async def test_revoked_partition_is_left_for_new_owner_to_replay(self, monkeypatch):
        monkeypatch.setattr("libs.events.consumer.RETRY_DELAY_SECONDS", 0)
        broker = self.FakeKafka([self.FakeMessage(_categorized().model_dump_json())])

        async def handler(event):
            broker.assigned.clear()
            raise RuntimeError("partition revoked during processing")

        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)
        consumer._consumer = broker

        await consumer._run()

        assert broker.commits == []

    async def test_revocation_resumes_polling_when_aiokafka_retains_assignment(self, monkeypatch):
        """Model the real subscription state, which retains old tps during rejoin."""
        from aiokafka import AIOKafkaConsumer

        monkeypatch.setattr("libs.events.consumer.RETRY_DELAY_SECONDS", 0)
        kafka = AIOKafkaConsumer("transactions.events", group_id="test")
        partition = TopicPartition("transactions.events", 0)
        kafka._subscription.assign_from_subscribed({partition})
        calls = []
        consumer = None

        async def handler(event):
            calls.append(event.transaction_id)
            if len(calls) == 1:
                kafka._subscription.begin_reassignment()
                await consumer.on_partitions_revoked({partition})
                assert partition in kafka.assignment()  # old guard would retry forever
                raise RuntimeError("database down until rebalance")

        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)

        class RejoiningKafka(self.FakeKafka):
            async def __anext__(self):
                if calls:
                    # A new poll permits aiokafka to rejoin; replay the held offset.
                    await consumer.on_partitions_assigned({partition})
                return await super().__anext__()

            def assignment(self):
                return kafka.assignment()

        broker = RejoiningKafka(
            [self.FakeMessage(_categorized().model_dump_json()) for _ in range(2)]
        )
        consumer._consumer = broker
        try:
            await asyncio.wait_for(consumer._run(), timeout=1)
        finally:
            await kafka.stop()
        assert calls == ["tx_1", "tx_1"]
        assert broker.commits == [{partition: 1}]

    async def test_reassigned_partition_cannot_commit_old_inflight_record(self, monkeypatch):
        monkeypatch.setattr("libs.events.consumer.RETRY_DELAY_SECONDS", 0)
        partition = TopicPartition("transactions.events", 0)
        consumer = None

        async def handler(event):
            await consumer.on_partitions_revoked({partition})
            await consumer.on_partitions_assigned({partition})

        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)
        broker = self.FakeKafka([self.FakeMessage(_categorized().model_dump_json())])
        consumer._consumer = broker
        await consumer._run()
        assert broker.commits == []

    async def test_shutdown_cancels_retry_without_committing(self):
        attempted = asyncio.Event()

        async def handler(event):
            attempted.set()
            raise RuntimeError("database unavailable")

        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)
        broker = self.FakeKafka([self.FakeMessage(_categorized().model_dump_json())])
        broker.stop = AsyncMock()
        consumer._consumer = broker
        consumer._task = asyncio.create_task(consumer._run())
        await asyncio.wait_for(attempted.wait(), timeout=1)

        await consumer.stop()

        assert broker.commits == []
        broker.stop.assert_awaited_once()
        assert consumer._task is None

    async def test_unknown_event_type_is_committed_and_skipped(self):
        async def handler(event):  # pragma: no cover - must not be called
            raise AssertionError("handler should not run")

        consumer = self._consumer(handler)
        await consumer._handle_message(
            self.FakeMessage(json.dumps({"event_type": "something.new"}))
        )

        assert consumer.commits == 1
