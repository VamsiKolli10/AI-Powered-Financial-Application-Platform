import ssl

import pytest

from libs.common.config import Settings


def test_local_environment_skips_secret_checks():
    # The test process exports weak dev secrets; local must accept them.
    settings = Settings(environment="local", jwt_secret="short")
    assert settings.is_local


@pytest.mark.parametrize("key", ["", "sk-replace-me", " your-api-key "])
def test_placeholder_keys_do_not_configure_the_ai_provider(key):
    from libs.llm_client.factory import build_responder

    settings = Settings(llm_enabled=True, openai_api_key=key)
    assert not settings.has_llm_credentials
    assert build_responder(settings) is None


def test_production_rejects_default_jwt_secret():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(environment="production")


def test_production_rejects_default_internal_token():
    with pytest.raises(ValueError, match="INTERNAL_SERVICE_TOKEN"):
        Settings(
            environment="production",
            jwt_secret="x" * 40,
            internal_service_token="dev-internal-token",
        )


def test_production_requires_openai_key_when_llm_enabled():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings(
            environment="production",
            jwt_secret="x" * 40,
            internal_service_token="real-token",
            llm_enabled=True,
            openai_api_key="",
        )


def test_valid_production_settings_pass():
    settings = Settings(
        environment="production",
        jwt_secret="x" * 40,
        internal_service_token="real-token",
        llm_enabled=False,
    )
    assert not settings.is_local


def test_kafka_sasl_options_include_scram_credentials():
    settings = Settings(
        environment="staging",
        jwt_secret="x" * 40,
        internal_service_token="real-token",
        llm_enabled=False,
        kafka_security_protocol="SASL_SSL",
        kafka_sasl_username="finai",
        kafka_sasl_password="secret",
    )

    options = settings.kafka_client_options
    context = options.pop("ssl_context")
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert options == {
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "SCRAM-SHA-512",
        "sasl_plain_username": "finai",
        "sasl_plain_password": "secret",
    }


@pytest.mark.parametrize("protocol", ["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"])
async def test_kafka_options_construct_real_clients_without_connecting(protocol):
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

    options = Settings(
        _env_file=None,
        environment="test",
        kafka_security_protocol=protocol,
        kafka_sasl_username="test-user",
        kafka_sasl_password="test-password",
    ).kafka_client_options
    assert ("ssl_context" in options) == (protocol in ("SSL", "SASL_SSL"))
    producer = AIOKafkaProducer(**options)
    try:
        consumer = AIOKafkaConsumer(**options)
        await consumer.stop()
    finally:
        await producer.stop()


def test_kafka_sasl_requires_credentials_outside_local():
    with pytest.raises(ValueError, match="Kafka SASL credentials"):
        Settings(
            environment="production",
            jwt_secret="x" * 40,
            internal_service_token="real-token",
            llm_enabled=False,
            kafka_enabled=True,
            kafka_security_protocol="SASL_SSL",
        )
