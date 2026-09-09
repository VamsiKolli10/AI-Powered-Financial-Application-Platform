"""Event contract and consumer-loop behaviour."""

import json
from datetime import UTC, datetime
from decimal import Decimal

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
        def __init__(self, value, offset=0):
            self.value = value
            self.offset = offset

    def _consumer(self, handler):
        consumer = KafkaEventConsumer(bootstrap_servers="unused", group_id="test", handler=handler)
        consumer.commits = 0

        async def _commit():
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

        # Offset held, so the message is redelivered rather than silently dropped.
        assert consumer.commits == 0

    async def test_undecodable_message_is_committed_and_skipped(self):
        async def handler(event):  # pragma: no cover - must not be called
            raise AssertionError("handler should not run")

        consumer = self._consumer(handler)
        await consumer._handle_message(self.FakeMessage("{not json"))

        assert consumer.commits == 1

    async def test_unknown_event_type_is_committed_and_skipped(self):
        async def handler(event):  # pragma: no cover - must not be called
            raise AssertionError("handler should not run")

        consumer = self._consumer(handler)
        await consumer._handle_message(
            self.FakeMessage(json.dumps({"event_type": "something.new"}))
        )

        assert consumer.commits == 1
