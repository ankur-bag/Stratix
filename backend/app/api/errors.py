from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from app.core.logging import get_logger
from app.core.request_id import REQUEST_ID_HEADER, get_request_id

logger = get_logger(__name__)


class ErrorBody(BaseModel):
    """Stable public representation of an API error."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class ErrorEnvelope(BaseModel):
    """Top-level envelope shared by every API error response."""

    error: ErrorBody


class AppError(Exception):
    """An expected application error safe to expose to API clients."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


def error_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> JSONResponse:
    """Build the stable API error envelope without leaking internal details."""
    request_id = getattr(request.state, "request_id", get_request_id())
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            details=dict(details or {}),
            request_id=request_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: request_id},
    )


def register_error_handlers(application: FastAPI) -> None:
    """Register handlers that normalize expected and unexpected API errors."""

    @application.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(
            request=request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            request=request,
            status_code=422,
            code="REQUEST_VALIDATION_ERROR",
            message="The request could not be validated.",
            details={"errors": exc.errors()},
        )

    @application.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        message = (
            exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
        )
        return error_response(
            request=request,
            status_code=exc.status_code,
            code=f"HTTP_{exc.status_code}",
            message=message,
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            request_id=getattr(request.state, "request_id", get_request_id()),
            error_type=type(exc).__name__,
        )
        return error_response(
            request=request,
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected error occurred.",
        )
