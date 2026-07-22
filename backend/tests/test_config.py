from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_read_dotenv_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=Configured Stratix\nLOG_LEVEL=warning\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.app_name == "Configured Stratix"
    assert settings.log_level == "WARNING"


def test_settings_reject_invalid_api_prefix() -> None:
    with pytest.raises(ValidationError):
        Settings(api_v1_prefix="api/v1", _env_file=None)


def test_settings_validate_clerk_clock_skew() -> None:
    assert Settings(_env_file=None).clerk_clock_skew_ms == 10_000
    with pytest.raises(ValidationError, match="CLERK_CLOCK_SKEW_MS"):
        Settings(clerk_clock_skew_ms=120_001, _env_file=None)


def test_settings_reject_disabled_authentication_in_production() -> None:
    with pytest.raises(ValidationError, match="AUTH_DISABLED"):
        Settings(app_env="production", auth_disabled=True, _env_file=None)


def test_settings_validate_safe_crawler_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(crawler_max_pages=101, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(crawler_max_concurrency_per_domain=2, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(
            crawler_min_delay_seconds=8,
            crawler_max_delay_seconds=4,
            _env_file=None,
        )
