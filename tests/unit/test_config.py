import pytest

from libs.common.config import Settings


def test_local_environment_skips_secret_checks():
    # The test process exports weak dev secrets; local must accept them.
    settings = Settings(environment="local", jwt_secret="short")
    assert settings.is_local


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

    assert settings.kafka_client_options == {
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "SCRAM-SHA-512",
        "sasl_plain_username": "finai",
        "sasl_plain_password": "secret",
    }


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
