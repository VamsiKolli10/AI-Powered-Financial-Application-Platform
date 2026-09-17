"""Notifications service: consumes transaction events, raises alerts."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI

from libs.common.app import create_app
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.db.session import check_database, dispose_engine, session_scope
from libs.events import KafkaEventConsumer
from services.notifications.delivery import LoggingChannel
from services.notifications.handlers import NotificationHandler
from services.notifications.routes import router
from services.notifications.rules import AlertThresholds

settings = get_settings()
log = get_logger("notifications")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    handler = NotificationHandler(
        session_scope=session_scope,
        channel=LoggingChannel(),
        thresholds=AlertThresholds(
            large_transaction=Decimal(str(settings.large_transaction_threshold)),
            budget_monthly_limit=Decimal(str(settings.budget_monthly_limit)),
        ),
    )
    app.state.handler = handler

    consumer: KafkaEventConsumer | None = None
    if settings.kafka_enabled:
        consumer = KafkaEventConsumer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=f"{settings.kafka_consumer_group}.notifications",
            handler=handler,
            client_options=settings.kafka_client_options,
        )
        await consumer.start()
    else:
        # Without a broker the read API still works; nothing produces new alerts.
        log.info("kafka_disabled_consumer_not_started")
    app.state.consumer = consumer

    yield

    if consumer is not None:
        await consumer.stop()
    await dispose_engine()


app = create_app(
    service_name="notifications",
    title="Notifications Service",
    description=(
        "Consumes transaction events and turns them into alerts: anomalies, large "
        "transactions and budget thresholds. Alerts are read through this API."
    ),
    settings=settings,
    readiness_checks={"database": check_database},
    lifespan=lifespan,
)
app.include_router(router)
