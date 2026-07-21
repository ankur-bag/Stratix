from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.auth import AuthenticatedContext, ClerkAuthenticationVerifier
from app.core.config import Settings
from app.main import create_app
from tests.fakes import InMemoryPersistence, RecordingBackgroundExecutor


@dataclass
class FakeReason:
    value: tuple[str, str]


@dataclass
class FakeRequestState:
    is_signed_in: bool
    reason: object | None = None
    payload: dict[str, Any] | None = None


class FakeClerkClient:
    def __init__(self, state: FakeRequestState | Exception) -> None:
        self.state = state
        self.requests: list[Request] = []

    def authenticate_request(self, request: Request, options: object) -> FakeRequestState:
        del options
        self.requests.append(request)
        if isinstance(self.state, Exception):
            raise self.state
        return self.state


class StaticVerifier:
    def __init__(self, context: AuthenticatedContext) -> None:
        self.context = context

    def authenticate(self, request: Request) -> AuthenticatedContext:
        del request
        return self.context


def auth_client(state: FakeRequestState | Exception) -> TestClient:
    verifier = ClerkAuthenticationVerifier(
        secret_key="sk_test_placeholder",
        jwt_key="public-key-placeholder",
        authorized_parties=["http://localhost:3000"],
        require_organization=True,
        client=FakeClerkClient(state),
    )
    application = create_app(
        Settings(app_env="testing", _env_file=None),
        persistence=InMemoryPersistence(),
        background_executor=RecordingBackgroundExecutor(),
        authentication_verifier=verifier,
    )
    return TestClient(application)


def error_code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def test_missing_and_malformed_authorization_headers() -> None:
    state = FakeRequestState(is_signed_in=True, payload={"sub": "user", "org_id": "org"})
    with auth_client(state) as client:
        missing = client.get("/api/v1/onboarding/status")
        malformed = client.get(
            "/api/v1/onboarding/status", headers={"Authorization": "Token not-a-bearer"}
        )

    assert missing.status_code == 401
    assert error_code(missing) == "AUTHENTICATION_REQUIRED"
    assert malformed.status_code == 401
    assert error_code(malformed) == "INVALID_SESSION_TOKEN"


def test_invalid_expired_and_wrong_party_tokens_use_stable_errors() -> None:
    cases = [
        ("token-invalid", "INVALID_SESSION_TOKEN"),
        ("token-expired", "SESSION_EXPIRED"),
        ("token-invalid-authorized-parties", "INVALID_AUTHORIZED_PARTY"),
    ]
    for reason, expected in cases:
        state = FakeRequestState(
            is_signed_in=False,
            reason=FakeReason((reason, "raw provider detail must not escape")),
        )
        with auth_client(state) as client:
            response = client.get(
                "/api/v1/onboarding/status",
                headers={"Authorization": "Bearer sensitive-session-token"},
            )

        assert response.status_code == 401
        assert error_code(response) == expected
        assert "sensitive-session-token" not in response.text
        assert "raw provider detail" not in response.text


def test_signed_in_user_requires_active_organization() -> None:
    state = FakeRequestState(is_signed_in=True, payload={"sub": "user_123", "sid": "sess_123"})
    with auth_client(state) as client:
        response = client.get(
            "/api/v1/onboarding/status", headers={"Authorization": "Bearer valid-token"}
        )

    assert response.status_code == 403
    assert error_code(response) == "ORGANIZATION_REQUIRED"


def test_valid_context_extracts_role_and_permissions() -> None:
    state = FakeRequestState(
        is_signed_in=True,
        payload={
            "sub": "user_123",
            "org_id": "org_123",
            "sid": "sess_123",
            "org_role": "org:member",
            "org_permissions": ["org:competitors:manage", 42],
        },
    )
    verifier = ClerkAuthenticationVerifier(
        secret_key="sk_test_placeholder",
        jwt_key="public-key-placeholder",
        authorized_parties=["http://localhost:3000"],
        require_organization=True,
        client=FakeClerkClient(state),
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer valid-token")],
        }
    )

    context = verifier.authenticate(request)

    assert context == AuthenticatedContext(
        user_id="user_123",
        organization_id="org_123",
        session_id="sess_123",
        organization_role="org:member",
        organization_permissions=["org:competitors:manage"],
    )


def test_development_auth_fallback_is_explicit() -> None:
    application = create_app(
        Settings(
            app_env="development",
            auth_disabled=True,
            dev_user_id="user_dev",
            dev_organization_id="org_dev",
            _env_file=None,
        ),
        persistence=InMemoryPersistence(),
        background_executor=RecordingBackgroundExecutor(),
    )
    with TestClient(application) as client:
        response = client.get("/api/v1/onboarding/status")

    assert response.status_code == 200
    assert response.json()["has_company_profile"] is False


def test_cors_allows_configured_origin_and_rejects_other_origin() -> None:
    application = create_app(
        Settings(
            app_env="testing",
            frontend_allowed_origins=["http://localhost:3000"],
            _env_file=None,
        ),
        persistence=InMemoryPersistence(),
        background_executor=RecordingBackgroundExecutor(),
        authentication_verifier=StaticVerifier(
            AuthenticatedContext(user_id="user", organization_id="org")
        ),
    )
    headers = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization,Content-Type,Idempotency-Key,X-Request-ID",
    }
    with TestClient(application) as client:
        allowed = client.options(
            "/api/v1/onboarding/company",
            headers={**headers, "Origin": "http://localhost:3000"},
        )
        rejected = client.options(
            "/api/v1/onboarding/company",
            headers={**headers, "Origin": "https://untrusted.example"},
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_production_readiness_requires_clerk_configuration() -> None:
    application = create_app(
        Settings(
            app_env="production",
            auth_disabled=False,
            clerk_secret_key=None,
            clerk_jwt_key=None,
            _env_file=None,
        ),
        persistence=InMemoryPersistence(),
        background_executor=RecordingBackgroundExecutor(),
    )
    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert error_code(response) == "AUTH_CONFIGURATION_INVALID"
