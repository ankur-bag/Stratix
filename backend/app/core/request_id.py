from contextvars import ContextVar, Token
from uuid import UUID, uuid4

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def create_request_id(candidate: str | None = None) -> str:
    """Return a canonical client UUID or generate a fresh correlation ID."""
    if candidate is not None:
        try:
            return str(UUID(candidate))
        except ValueError:
            pass
    return str(uuid4())


def set_request_id(request_id: str) -> Token[str | None]:
    """Bind a request ID to the current async context."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the request ID context after a request completes."""
    _request_id.reset(token)


def get_request_id() -> str:
    """Return the current correlation ID, generating one outside request scope."""
    return _request_id.get() or create_request_id()
