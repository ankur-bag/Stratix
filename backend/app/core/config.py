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
    document_storage_provider: Literal["local", "s3", "fake"] = "local"
    document_storage_path: str = ".data/company-documents"
    document_s3_bucket: str | None = None
    document_s3_endpoint_url: str | None = None
    document_s3_region: str = "us-east-1"
    document_upload_url_ttl_seconds: int = 900
    document_max_files_per_company: int = 10
    document_onboarding_max_files: int = 1
    document_max_file_size_bytes: int = 20_971_520
    document_max_pages: int = 100
    document_max_extracted_characters: int = 2_000_000
    document_chunk_target_characters: int = 3500
    document_chunk_max_characters: int = 5000
    document_chunk_overlap_characters: int = 500
    document_chunk_min_characters: int = 150
    document_ingestion_max_concurrency: int = 2
    document_database_batch_size: int = 100
    document_progress_update_interval: int = 10
    gemini_api_key: str | None = None
    embeddings_enabled: bool = False
    embedding_provider: Literal["gemini"] = "gemini"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    embedding_document_task_type: str = "RETRIEVAL_DOCUMENT"
    embedding_query_task_type: str = "RETRIEVAL_QUERY"
    embedding_batch_size: int = 32
    embedding_max_concurrency: int = 2
    embedding_max_retries: int = 3
    embedding_retry_base_seconds: float = 1
    embedding_version: str = "gemini-embedding-001-768-v1"
    vector_db_provider: Literal["pinecone", "disabled", "fake"] = "pinecone"
    vector_search_enabled: bool = False
    vector_search_required: bool = False
    pinecone_api_key: str | None = None
    pinecone_index_name: str = "stratix-knowledge"
    pinecone_host: str | None = None
    pinecone_dimensions: int = 768
    pinecone_metric: Literal["cosine"] = "cosine"
    pinecone_namespace_prefix: str = "stratix-org-"
    pinecone_upsert_batch_size: int = 100
    pinecone_max_concurrency: int = 2
    pinecone_max_retries: int = 3
    pinecone_retry_base_seconds: float = 1
    pinecone_default_top_k: int = 8
    pinecone_max_top_k: int = 30
    pinecone_query_overfetch_factor: int = 3

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

    @field_validator(
        "clerk_secret_key",
        "clerk_jwt_key",
        "tavily_api_key",
        "gemini_api_key",
        "pinecone_api_key",
        "pinecone_host",
        "document_s3_bucket",
        "document_s3_endpoint_url",
        mode="before",
    )
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

    @model_validator(mode="after")
    def validate_document_intelligence_configuration(self) -> "Settings":
        positive_values = (
            self.document_upload_url_ttl_seconds,
            self.document_max_files_per_company,
            self.document_onboarding_max_files,
            self.document_max_file_size_bytes,
            self.document_max_pages,
            self.document_max_extracted_characters,
            self.document_chunk_target_characters,
            self.document_chunk_max_characters,
            self.document_chunk_min_characters,
            self.document_ingestion_max_concurrency,
            self.document_database_batch_size,
            self.document_progress_update_interval,
            self.embedding_dimensions,
            self.embedding_batch_size,
            self.embedding_max_concurrency,
            self.pinecone_dimensions,
            self.pinecone_upsert_batch_size,
            self.pinecone_max_concurrency,
            self.pinecone_default_top_k,
            self.pinecone_max_top_k,
            self.pinecone_query_overfetch_factor,
        )
        if any(value <= 0 for value in positive_values):
            raise ValueError("Document intelligence limits must be positive")
        if self.document_onboarding_max_files > self.document_max_files_per_company:
            raise ValueError("Onboarding document limit cannot exceed the company limit")
        if self.document_chunk_target_characters > self.document_chunk_max_characters:
            raise ValueError("Document chunk target cannot exceed the maximum")
        if not 0 <= self.document_chunk_overlap_characters < self.document_chunk_target_characters:
            raise ValueError("Document chunk overlap must be smaller than the target")
        if self.embedding_dimensions != 768 or self.pinecone_dimensions != 768:
            raise ValueError("Phase 4.5 requires 768-dimensional embeddings and vectors")
        if self.embedding_dimensions != self.pinecone_dimensions:
            raise ValueError("Embedding and Pinecone dimensions must match")
        if self.pinecone_default_top_k > self.pinecone_max_top_k:
            raise ValueError("Default vector top-k cannot exceed the maximum")
        if self.embeddings_enabled and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when embeddings are enabled")
        if (
            self.vector_search_enabled
            and self.vector_db_provider == "pinecone"
            and (not self.pinecone_api_key or not self.pinecone_host)
        ):
            raise ValueError(
                "PINECONE_API_KEY and PINECONE_HOST are required when vector search is enabled"
            )
        if self.vector_search_required and not self.vector_search_enabled:
            raise ValueError("Required vector search must be enabled")
        if self.vector_search_enabled and not self.embeddings_enabled:
            raise ValueError("Vector search requires embeddings")
        if self.vector_search_required and self.vector_db_provider == "disabled":
            raise ValueError("Required vector search cannot use the disabled provider")
        if self.document_storage_provider == "s3" and not self.document_s3_bucket:
            raise ValueError("DOCUMENT_S3_BUCKET is required for S3 document storage")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
