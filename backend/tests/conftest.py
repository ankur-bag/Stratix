from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Provide an in-process client with no external service dependencies."""
    settings = Settings(app_env="testing", app_debug=False, _env_file=None)
    with TestClient(create_app(settings)) as test_client:
        yield test_client
