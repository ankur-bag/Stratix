from uuid import UUID, uuid4

from fastapi.testclient import TestClient


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    UUID(response.headers["X-Request-ID"])


def test_readiness(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


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
