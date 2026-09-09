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
