from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.fakes import InMemoryPersistence, RecordingBackgroundExecutor


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    UUID(response.headers["X-Request-ID"])


def test_readiness(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "intelligence": {
            "indexing_enabled": True,
            "indexing_required": False,
            "embeddings_available": False,
            "vector_store_available": False,
        },
        "strategic_agent": {
            "enabled": False,
            "configured": False,
            "provider": "google_genai",
            "provider_available": False,
            "agent_version": "stratix-strategic-agent-v1",
            "prompt_version": "strategy-prompt-v1",
            "tool_schema_version": "strategy-tools-v1",
            "active_runs": 0,
            "failed_runs": 0,
            "citation_validation_failures": 0,
            "budget_exhaustion_count": 0,
        },
    }


def test_valid_correlation_id_is_echoed(client: TestClient) -> None:
    request_id = str(uuid4())

    response = client.get("/health/live", headers={"X-Request-ID": request_id})

    assert response.headers["X-Request-ID"] == request_id


def test_not_found_uses_stable_error_envelope(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "HTTP_404",
            "message": "Not Found",
            "details": {},
            "request_id": response.headers["X-Request-ID"],
        }
    }


def test_readiness_reports_database_failure() -> None:
    application = create_app(
        Settings(app_env="testing", _env_file=None),
        persistence=InMemoryPersistence(ready=False),
        background_executor=RecordingBackgroundExecutor(),
    )
    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
