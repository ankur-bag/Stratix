from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_authenticated_context
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.main import create_app
from tests.fakes import (
    FakeWebsiteCrawler,
    InMemoryPersistence,
    RecordingBackgroundExecutor,
    RecordingCompetitorScanExecutor,
)


@pytest.fixture
def persistence() -> InMemoryPersistence:
    return InMemoryPersistence()


@pytest.fixture
def background_executor() -> RecordingBackgroundExecutor:
    return RecordingBackgroundExecutor()


@pytest.fixture
def discovery_background_executor() -> RecordingBackgroundExecutor:
    return RecordingBackgroundExecutor()


@pytest.fixture
def competitor_scan_executor() -> RecordingCompetitorScanExecutor:
    return RecordingCompetitorScanExecutor()


@pytest.fixture
def application(
    persistence: InMemoryPersistence,
    background_executor: RecordingBackgroundExecutor,
    discovery_background_executor: RecordingBackgroundExecutor,
    competitor_scan_executor: RecordingCompetitorScanExecutor,
) -> FastAPI:
    settings = Settings(
        app_env="testing",
        app_debug=False,
        background_jobs_enabled=False,
        scheduler_enabled=False,
        _env_file=None,
    )
    app = create_app(
        settings,
        persistence=persistence,
        background_executor=background_executor,
        discovery_background_executor=discovery_background_executor,
        crawler=FakeWebsiteCrawler(),
        competitor_scan_executor=competitor_scan_executor,
    )

    async def test_context() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="user_test", organization_id="org_test")

    app.dependency_overrides[get_authenticated_context] = test_context
    return app


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    """Provide an in-process client with no external service dependencies."""
    with TestClient(application) as test_client:
        yield test_client
