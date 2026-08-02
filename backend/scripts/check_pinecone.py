"""Safely inspect and validate the configured Pinecone index."""

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pinecone import PineconeAsyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings, get_settings


class PineconeDiagnosticError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PineconeValidation:
    index_name: str
    ready: bool
    dimensions: int
    metric: str


ClientFactory = Callable[..., Any]


def status_lines(settings: Settings) -> list[str]:
    enabled = settings.vector_search_enabled and settings.vector_db_provider == "pinecone"
    configured = bool(settings.pinecone_api_key and settings.pinecone_host)
    return [
        f"enabled={str(enabled).lower()}",
        f"configured={str(configured).lower()}",
        f"index_name={settings.pinecone_index_name}",
        f"expected_dimensions={settings.pinecone_dimensions}",
        f"expected_metric={settings.pinecone_metric}",
        f"host_configured={str(bool(settings.pinecone_host)).lower()}",
    ]


def require_live_configuration(settings: Settings) -> None:
    if not settings.vector_search_enabled or settings.vector_db_provider != "pinecone":
        raise PineconeDiagnosticError(
            "PINECONE_DISABLED",
            "Pinecone vector search is disabled in the runtime configuration.",
        )
    if not settings.pinecone_api_key:
        raise PineconeDiagnosticError(
            "PINECONE_API_KEY_MISSING",
            "The Pinecone API key is not configured.",
        )
    if not settings.pinecone_host:
        raise PineconeDiagnosticError(
            "PINECONE_HOST_MISSING",
            "The Pinecone index host is not configured.",
        )


async def validate(
    settings: Settings,
    *,
    client_factory: ClientFactory = PineconeAsyncio,
) -> PineconeValidation:
    client = await _validated_client(settings, client_factory)
    try:
        description = await _describe(client, settings.pinecone_index_name)
        return _validate_description(settings, description)
    finally:
        await _close(client)


async def smoke_test(
    settings: Settings,
    *,
    confirmed: bool,
    client_factory: ClientFactory = PineconeAsyncio,
) -> PineconeValidation:
    if not confirmed:
        raise PineconeDiagnosticError(
            "PINECONE_SMOKE_GUARD_REQUIRED",
            "Pass --confirm-temporary-write to authorize the temporary diagnostic vector.",
        )
    client = await _validated_client(settings, client_factory)
    index: Any | None = None
    namespace = f"stratix-diagnostic-smoke-{uuid4()}"
    vector_id = f"diagnostic-{uuid4()}"
    try:
        description = await _describe(client, settings.pinecone_index_name)
        validation = _validate_description(settings, description)
        index = client.IndexAsyncio(host=settings.pinecone_host)
        vector = [0.0] * settings.pinecone_dimensions
        vector[0] = 1.0
        await index.upsert(
            namespace=namespace,
            vectors=[
                {
                    "id": vector_id,
                    "values": vector,
                    "metadata": {"purpose": "stratix-explicit-diagnostic"},
                }
            ],
        )
        response = await index.query(
            namespace=namespace,
            vector=vector,
            top_k=1,
            include_values=False,
            include_metadata=True,
        )
        matches = _value(response, "matches") or []
        if not matches or _value(matches[0], "id") != vector_id:
            raise PineconeDiagnosticError(
                "PINECONE_SMOKE_QUERY_FAILED",
                "The temporary diagnostic vector could not be queried.",
            )
        return validation
    except PineconeDiagnosticError:
        raise
    except Exception as exc:
        raise PineconeDiagnosticError(
            "PINECONE_SMOKE_FAILED",
            "The temporary Pinecone diagnostic operation failed.",
        ) from exc
    finally:
        if index is not None:
            try:
                await index.delete(namespace=namespace, delete_all=True)
            finally:
                await _close(index)
        await _close(client)


async def _validated_client(settings: Settings, client_factory: ClientFactory) -> Any:
    require_live_configuration(settings)
    try:
        return client_factory(api_key=settings.pinecone_api_key)
    except Exception as exc:
        raise PineconeDiagnosticError(
            "PINECONE_UNAVAILABLE",
            "The configured Pinecone index is unavailable.",
        ) from exc


async def _describe(client: Any, index_name: str) -> Any:
    try:
        return await client.describe_index(index_name)
    except Exception as exc:
        raise PineconeDiagnosticError(
            "PINECONE_UNAVAILABLE",
            "The configured Pinecone index is unavailable.",
        ) from exc


def _validate_description(settings: Settings, description: Any) -> PineconeValidation:
    dimensions = _value(description, "dimension")
    metric = _value(description, "metric")
    status = _value(description, "status") or {}
    ready = bool(_value(status, "ready"))
    if dimensions != settings.pinecone_dimensions:
        raise PineconeDiagnosticError(
            "PINECONE_DIMENSION_MISMATCH",
            "The Pinecone index dimension does not match the configured dimension.",
        )
    if metric != settings.pinecone_metric:
        raise PineconeDiagnosticError(
            "PINECONE_METRIC_MISMATCH",
            "The Pinecone index metric does not match the configured metric.",
        )
    if not ready:
        raise PineconeDiagnosticError(
            "PINECONE_INDEX_NOT_READY",
            "The configured Pinecone index is not ready.",
        )
    return PineconeValidation(
        index_name=settings.pinecone_index_name,
        ready=ready,
        dimensions=int(dimensions),
        metric=str(metric),
    )


def validation_lines(result: PineconeValidation) -> list[str]:
    return [
        f"index_name={result.index_name}",
        "reachable=true",
        f"ready={str(result.ready).lower()}",
        f"dimensions={result.dimensions}",
        f"metric={result.metric}",
        "validation=passed",
    ]


def _value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


async def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if close is not None:
        await close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Pinecone status and explicit live diagnostics."
    )
    parser.add_argument("command", choices=("status", "validate", "smoke-test"))
    parser.add_argument(
        "--confirm-temporary-write",
        action="store_true",
        help="Authorize one temporary vector upsert/query/delete in a diagnostic namespace.",
    )
    return parser


def cli(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    client_factory: ClientFactory = PineconeAsyncio,
) -> int:
    arguments = build_parser().parse_args(argv)
    runtime = settings or get_settings()
    try:
        if arguments.command == "status":
            lines = status_lines(runtime)
        elif arguments.command == "validate":
            result = asyncio.run(validate(runtime, client_factory=client_factory))
            lines = validation_lines(result)
        else:
            result = asyncio.run(
                smoke_test(
                    runtime,
                    confirmed=arguments.confirm_temporary_write,
                    client_factory=client_factory,
                )
            )
            lines = [
                *validation_lines(result),
                "smoke_test=passed",
                "temporary_namespace_deleted=true",
            ]
    except PineconeDiagnosticError as exc:
        print(f"error_code={exc.code}")
        print(f"message={exc.message}")
        return 2
    except Exception:
        print("error_code=PINECONE_DIAGNOSTIC_FAILED")
        print("message=The Pinecone diagnostic could not be completed safely.")
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
