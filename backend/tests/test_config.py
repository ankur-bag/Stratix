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


def test_settings_reject_disabled_authentication_in_production() -> None:
    with pytest.raises(ValidationError, match="AUTH_DISABLED"):
        Settings(app_env="production", auth_disabled=True, _env_file=None)
