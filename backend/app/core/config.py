from functools import lru_cache
from typing import Literal

from pydantic import field_validator
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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
