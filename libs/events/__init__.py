"""Kafka event contracts and helpers shared by every service."""

from libs.common.config import Settings
from libs.events.consumer import EventHandler, KafkaEventConsumer
from libs.events.publisher import (
    EventPublisher,
    InMemoryEventPublisher,
    KafkaEventPublisher,
    NullEventPublisher,
)
from libs.events.schemas import (
    SCHEMA_VERSION,
    BaseEvent,
    Topics,
    TransactionAnomalyFlagged,
    TransactionCategorized,
    parse_event,
)

__all__ = [
    "SCHEMA_VERSION",
    "BaseEvent",
    "EventHandler",
    "EventPublisher",
    "InMemoryEventPublisher",
    "KafkaEventConsumer",
    "KafkaEventPublisher",
    "NullEventPublisher",
    "Topics",
    "TransactionAnomalyFlagged",
    "TransactionCategorized",
    "parse_event",
]


def build_publisher(settings: Settings, *, client_id: str = "service") -> EventPublisher:
    """Publisher chosen by configuration: real broker, or a logging no-op."""
    if not settings.kafka_enabled:
        return NullEventPublisher()
    return KafkaEventPublisher(settings.kafka_bootstrap_servers, client_id=client_id)
