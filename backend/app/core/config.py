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
    clerk_secret_key: str | None = None
    clerk_jwt_key: str | None = None
    clerk_authorized_parties: list[str] = ["http://localhost:3000"]
    clerk_clock_skew_ms: int = 10_000
    clerk_require_organization: bool = True
    frontend_allowed_origins: list[str] = ["http://localhost:3000"]
    docs_enabled: bool = True
    auto_start_competitor_discovery: bool = True
    discovery_max_queries: int = 8
    discovery_max_results_per_query: int = 8
    discovery_category_weight: float = 0.22
    discovery_use_case_weight: float = 0.18
    discovery_segment_weight: float = 0.12
    discovery_keyword_weight: float = 0.14
    discovery_frequency_weight: float = 0.12
    discovery_provider_score_weight: float = 0.12
    discovery_known_competitor_weight: float = 0.10
    tavily_api_key: str | None = None
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
    auto_start_competitor_baseline: bool = True
    competitor_crawler_max_sources: int = 12
    competitor_crawler_max_depth: int = 1
    competitor_crawler_max_pages_per_source: int = 1
    competitor_crawler_max_concurrency_per_domain: int = 1
    competitor_crawler_min_delay_seconds: float = 4
    competitor_crawler_max_delay_seconds: float = 8
    competitor_crawler_max_retries: int = 2
    competitor_crawler_page_timeout_ms: int = 45000
    competitor_crawler_max_response_bytes: int = 5_000_000
    competitor_crawler_check_robots: bool = True
    change_min_significance_score: float = 0.25
    change_min_text_length: int = 20
    change_ignore_navigation: bool = True
    change_ignore_footer: bool = True
    change_ignore_cookie_banners: bool = True
    llm_enabled: bool = False

    @model_validator(mode="after")
    def reject_insecure_production_auth(self) -> "Settings":
        """Prevent the development identity from being enabled in production."""
        if self.app_env == "production" and self.auth_disabled:
            raise ValueError("AUTH_DISABLED cannot be true in production")
        if self.app_env == "production" and "*" in self.frontend_allowed_origins:
            raise ValueError("FRONTEND_ALLOWED_ORIGINS cannot contain '*' in production")
        return self

    @property
    def clerk_verification_configured(self) -> bool:
        """Return whether Clerk can verify sessions for this deployment."""
        has_verification_key = bool(self.clerk_jwt_key or self.clerk_secret_key)
        return has_verification_key and bool(self.clerk_authorized_parties)

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

    @field_validator("clerk_secret_key", "clerk_jwt_key", "tavily_api_key", mode="before")
    @classmethod
    def normalize_optional_secrets(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("clerk_authorized_parties", "frontend_allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().rstrip("/") for value in values))
        if any(not value.startswith(("http://", "https://")) for value in normalized):
            raise ValueError("Configured origins must use HTTP or HTTPS")
        return normalized

    @field_validator("mongodb_connect_timeout_ms", "mongodb_server_selection_timeout_ms")
    @classmethod
    def validate_positive_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("MongoDB timeouts must be positive")
        return value

    @field_validator("clerk_clock_skew_ms")
    @classmethod
    def validate_clerk_clock_skew(cls, value: int) -> int:
        if not 0 <= value <= 120_000:
            raise ValueError("CLERK_CLOCK_SKEW_MS must be between zero and 120000")
        return value

    @field_validator(
        "crawler_max_depth",
        "crawler_max_pages",
        "crawler_max_concurrency_per_domain",
        "crawler_page_timeout_ms",
        "crawler_max_response_bytes",
        "competitor_crawler_max_sources",
        "competitor_crawler_max_depth",
        "competitor_crawler_max_pages_per_source",
        "competitor_crawler_max_concurrency_per_domain",
        "competitor_crawler_page_timeout_ms",
        "competitor_crawler_max_response_bytes",
        "change_min_text_length",
    )
    @classmethod
    def validate_positive_crawler_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Crawler limits must be positive")
        return value

    @field_validator("discovery_max_queries", "discovery_max_results_per_query")
    @classmethod
    def validate_discovery_bounds(cls, value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError("Discovery query and result limits must be between 1 and 20")
        return value

    @field_validator("crawler_max_retries", "competitor_crawler_max_retries")
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
        if self.competitor_crawler_max_sources > 50:
            raise ValueError("Competitor source limit exceeds the safe maximum")
        if self.competitor_crawler_max_depth > 1:
            raise ValueError("Competitor crawl depth cannot exceed one")
        if self.competitor_crawler_max_pages_per_source != 1:
            raise ValueError("Phase 4 collects one page per competitor source")
        if self.competitor_crawler_max_concurrency_per_domain != 1:
            raise ValueError("Phase 4 supports one competitor request per domain")
        if self.competitor_crawler_min_delay_seconds < 0:
            raise ValueError("Competitor crawler minimum delay cannot be negative")
        if self.competitor_crawler_max_delay_seconds < self.competitor_crawler_min_delay_seconds:
            raise ValueError("Competitor crawler maximum delay must be at least the minimum")
        if not 0 <= self.change_min_significance_score <= 1:
            raise ValueError("CHANGE_MIN_SIGNIFICANCE_SCORE must be between zero and one")
        return self

    @model_validator(mode="after")
    def validate_discovery_weights(self) -> "Settings":
        weights = (
            self.discovery_category_weight,
            self.discovery_use_case_weight,
            self.discovery_segment_weight,
            self.discovery_keyword_weight,
            self.discovery_frequency_weight,
            self.discovery_provider_score_weight,
            self.discovery_known_competitor_weight,
        )
        if any(weight < 0 for weight in weights) or abs(sum(weights) - 1.0) > 0.0001:
            raise ValueError("Discovery score weights must be non-negative and sum to 1")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
