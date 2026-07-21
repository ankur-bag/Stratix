from collections.abc import Awaitable, Callable
from time import perf_counter

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import Message, Receive, Scope, Send

from app.core.logging import get_logger
from app.core.request_id import (
    REQUEST_ID_HEADER,
    create_request_id,
    reset_request_id,
    set_request_id,
)

logger = get_logger(__name__)


class RequestContextMiddleware:
    """Attach a correlation ID and emit one structured access log per request."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = create_request_id(headers.get(REQUEST_ID_HEADER))
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)
        context_tokens = structlog.contextvars.bind_contextvars(request_id=request_id)
        started_at = perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request_complete",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                duration_ms=round((perf_counter() - started_at) * 1000, 2),
            )
            structlog.contextvars.reset_contextvars(**context_tokens)
            reset_request_id(token)
