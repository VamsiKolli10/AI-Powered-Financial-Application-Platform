"""Central configuration, loaded from environment (see .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore"
    )

    # Core
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    service_name: str = "service"

    # Datastores
    database_url: str = "postgresql+asyncpg://finai:finai@localhost:5432/finai"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 5
    redis_url: str = "redis://localhost:6379/0"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_group: str = "finai"
    kafka_enabled: bool = True

    # LLM
    openai_api_key: str = ""
    # Override to point at Azure OpenAI, a gateway, or a local model server.
    llm_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 20.0
    llm_cache_ttl_seconds: int = 604_800
    llm_rate_limit_per_minute: int = 120
    llm_enabled: bool = True

    # Auth
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    internal_service_token: str = "dev-internal-token"

    # Alerting thresholds (Notifications service)
    anomaly_alert_threshold: float = 0.8
    large_transaction_threshold: float = 500.0
    budget_monthly_limit: float = 1500.0

    # Rate limiting
    rate_limit_standard: int = 100
    rate_limit_llm: int = 20

    # Downstream services (gateway)
    transactions_url: str = "http://localhost:8001"
    assistant_url: str = "http://localhost:8002"
    insights_url: str = "http://localhost:8003"
    notifications_url: str = "http://localhost:8004"

    @property
    def is_local(self) -> bool:
        return self.environment in ("local", "test")

    @model_validator(mode="after")
    def _reject_dev_secrets_outside_local(self) -> "Settings":
        """Fail fast rather than deploy with the development defaults."""
        if self.environment in ("staging", "production"):
            if self.jwt_secret in ("", "dev-only-change-me") or len(self.jwt_secret) < 32:
                raise ValueError(
                    "JWT_SECRET must be set to a value of at least 32 characters outside local."
                )
            if self.internal_service_token in ("", "dev-internal-token"):
                raise ValueError("INTERNAL_SERVICE_TOKEN must be set outside local.")
            if self.llm_enabled and not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY is required when LLM_ENABLED is true.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
