from collections.abc import Mapping
from typing import Any, Protocol, cast

from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
from pydantic import BaseModel, Field
from starlette.requests import Request


class AuthenticatedContext(BaseModel):
    """Trusted user and organization identity resolved at the API boundary."""

    user_id: str
    organization_id: str
    session_id: str | None = None
    organization_role: str | None = None
    organization_permissions: list[str] = Field(default_factory=list)


class AuthenticationFailure(Exception):
    """Safe authentication failure raised by verifier implementations."""

    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AuthenticationVerifier(Protocol):
    """Replaceable boundary for resolving a trusted request identity."""

    def authenticate(self, request: Request) -> AuthenticatedContext: ...


class ClerkRequestState(Protocol):
    is_signed_in: bool
    reason: object | None
    payload: Mapping[str, Any] | None


class ClerkClient(Protocol):
    def authenticate_request(
        self, request: Request, options: AuthenticateRequestOptions
    ) -> ClerkRequestState: ...


class DevelopmentAuthenticationVerifier:
    """Return the explicitly configured local-only development identity."""

    def __init__(self, *, user_id: str, organization_id: str) -> None:
        self._context = AuthenticatedContext(
            user_id=user_id,
            organization_id=organization_id,
            organization_role="org:admin",
        )

    def authenticate(self, request: Request) -> AuthenticatedContext:
        del request
        return self._context


class UnavailableAuthenticationVerifier:
    """Reject requests when server-side Clerk verification is not configured."""

    def authenticate(self, request: Request) -> AuthenticatedContext:
        del request
        raise AuthenticationFailure(
            code="AUTH_CONFIGURATION_INVALID",
            message="Authentication is temporarily unavailable.",
            status_code=503,
        )


class ClerkAuthenticationVerifier:
    """Verify Clerk session tokens without trusting unverified JWT claims."""

    def __init__(
        self,
        *,
        secret_key: str | None,
        jwt_key: str | None,
        authorized_parties: list[str],
        require_organization: bool,
        clock_skew_ms: int = 5_000,
        client: ClerkClient | None = None,
    ) -> None:
        self._client = client or cast(ClerkClient, Clerk(bearer_auth=secret_key))
        self._options = AuthenticateRequestOptions(
            secret_key=secret_key,
            jwt_key=jwt_key,
            authorized_parties=authorized_parties,
            clock_skew_in_ms=clock_skew_ms,
            accepts_token=["session_token"],
        )
        self._require_organization = require_organization

    def authenticate(self, request: Request) -> AuthenticatedContext:
        authorization = request.headers.get("Authorization")
        if authorization is None:
            raise AuthenticationFailure(
                code="AUTHENTICATION_REQUIRED",
                message="Authentication is required.",
                status_code=401,
            )
        scheme, separator, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not separator or not token.strip():
            raise AuthenticationFailure(
                code="INVALID_SESSION_TOKEN",
                message="The session token is invalid.",
                status_code=401,
            )

        try:
            state = self._client.authenticate_request(request, self._options)
        except Exception as exc:
            raise AuthenticationFailure(
                code="INVALID_SESSION_TOKEN",
                message="The session token could not be verified.",
                status_code=401,
            ) from exc
        if not state.is_signed_in:
            self._raise_for_reason(state.reason)
        payload = state.payload or {}
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise AuthenticationFailure(
                code="INVALID_SESSION_TOKEN",
                message="The session token is invalid.",
                status_code=401,
            )
        organization_id = payload.get("org_id")
        if not isinstance(organization_id, str) or not organization_id:
            if self._require_organization:
                raise AuthenticationFailure(
                    code="ORGANIZATION_REQUIRED",
                    message="Select an active organization to continue.",
                    status_code=403,
                )
            organization_id = ""
        permissions = payload.get("org_permissions")
        normalized_permissions = (
            [item for item in permissions if isinstance(item, str)]
            if isinstance(permissions, list)
            else []
        )
        return AuthenticatedContext(
            user_id=user_id,
            organization_id=organization_id,
            session_id=self._optional_string(payload.get("sid")),
            organization_role=self._optional_string(payload.get("org_role")),
            organization_permissions=normalized_permissions,
        )

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _reason_code(reason: object | None) -> str | None:
        value = getattr(reason, "value", None)
        if isinstance(value, tuple) and value and isinstance(value[0], str):
            return value[0]
        return None

    @classmethod
    def _raise_for_reason(cls, reason: object | None) -> None:
        reason_code = cls._reason_code(reason)
        if reason_code == "session-token-missing":
            code, message, status_code = (
                "AUTHENTICATION_REQUIRED",
                "Authentication is required.",
                401,
            )
        elif reason_code == "token-expired":
            code, message, status_code = (
                "SESSION_EXPIRED",
                "The session has expired.",
                401,
            )
        elif reason_code == "token-invalid-authorized-parties":
            code, message, status_code = (
                "INVALID_AUTHORIZED_PARTY",
                "The session token was not issued for an authorized application.",
                401,
            )
        elif reason_code in {
            "secret-key-missing",
            "jwk-failed-to-load",
            "jwk-remote-invalid",
            "jwk-failed-to-resolve",
            "server-error",
        }:
            code, message, status_code = (
                "AUTH_CONFIGURATION_INVALID",
                "Authentication is temporarily unavailable.",
                503,
            )
        else:
            code, message, status_code = (
                "INVALID_SESSION_TOKEN",
                "The session token is invalid.",
                401,
            )
        raise AuthenticationFailure(code=code, message=message, status_code=status_code)
