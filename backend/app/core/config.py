from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Stratix API"
    app_env: Literal["development", "testing", "production"] = "development"
    app_debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "stratix"
    mongodb_connect_timeout_ms: int = 5000
    mongodb_server_selection_timeout_ms: int = 5000
    auth_disabled: bool = False
    dev_user_id: str = "user_development"
    dev_organization_id: str = "org_development"
    crawler_user_agent: str = "StratixBot/0.1"
    crawler_max_depth: int = 2
    crawler_max_pages: int = 15
    crawler_max_concurrency_per_domain: int = 1
    crawler_min_delay_seconds: float = 4
    crawler_max_delay_seconds: float = 8
    crawler_max_retries: int = 2
    crawler_page_timeout_ms: int = 45000
    crawler_max_response_bytes: int = 5_000_000
    crawler_check_robots: bool = True
    llm_enabled: bool = False

    @model_validator(mode="after")
    def reject_insecure_production_auth(self) -> "Settings":
        """Prevent the development identity from being enabled in production."""
        if self.app_env == "production" and self.auth_disabled:
            raise ValueError("AUTH_DISABLED cannot be true in production")
        return self

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        """Require a normalized absolute API prefix."""
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("API_V1_PREFIX must start with '/' and must not end with '/'")
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """Normalize and validate the configured standard-library log level."""
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a valid logging level")
        return normalized

    @field_validator("app_name")
    @classmethod
    def validate_app_name(cls, value: str) -> str:
        """Reject empty application names."""
        if not value.strip():
            raise ValueError("APP_NAME must not be empty")
        return value.strip()

    @field_validator("mongodb_database", "dev_user_id", "dev_organization_id")
    @classmethod
    def validate_non_blank_settings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Configuration values must not be blank")
        return value.strip()

    @field_validator("mongodb_connect_timeout_ms", "mongodb_server_selection_timeout_ms")
    @classmethod
    def validate_positive_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("MongoDB timeouts must be positive")
        return value

    @field_validator(
        "crawler_max_depth",
        "crawler_max_pages",
        "crawler_max_concurrency_per_domain",
        "crawler_page_timeout_ms",
        "crawler_max_response_bytes",
    )
    @classmethod
    def validate_positive_crawler_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Crawler limits must be positive")
        return value

    @field_validator("crawler_max_retries")
    @classmethod
    def validate_retry_limit(cls, value: int) -> int:
        if not 0 <= value <= 5:
            raise ValueError("CRAWLER_MAX_RETRIES must be between 0 and 5")
        return value

    @model_validator(mode="after")
    def validate_crawler_configuration(self) -> "Settings":
        if self.crawler_max_depth > 5 or self.crawler_max_pages > 100:
            raise ValueError("Crawler depth or page limit exceeds the safe maximum")
        if self.crawler_max_concurrency_per_domain != 1:
            raise ValueError("Phase 2 supports one concurrent request per domain")
        if self.crawler_min_delay_seconds < 0:
            raise ValueError("CRAWLER_MIN_DELAY_SECONDS cannot be negative")
        if self.crawler_max_delay_seconds < self.crawler_min_delay_seconds:
            raise ValueError("Crawler maximum delay must be at least the minimum delay")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
