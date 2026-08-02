from types import SimpleNamespace
from typing import Any, ClassVar

from app.core.config import Settings
from scripts.check_pinecone import cli


class FakeIndex:
    deleted_namespaces: ClassVar[list[str]] = []

    def __init__(self) -> None:
        self.vector_id = ""
        self.closed = False

    async def upsert(self, *, namespace: str, vectors: list[dict[str, Any]]) -> None:
        assert namespace.startswith("stratix-diagnostic-smoke-")
        assert len(vectors) == 1
        assert len(vectors[0]["values"]) == 768
        self.vector_id = str(vectors[0]["id"])

    async def query(self, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["namespace"].startswith("stratix-diagnostic-smoke-")
        assert kwargs["include_values"] is False
        return SimpleNamespace(matches=[SimpleNamespace(id=self.vector_id)])

    async def delete(self, *, namespace: str, delete_all: bool) -> None:
        assert delete_all is True
        type(self).deleted_namespaces.append(namespace)

    async def close(self) -> None:
        self.closed = True


class FakeClient:
    description: ClassVar[Any] = SimpleNamespace(
        dimension=768,
        metric="cosine",
        status=SimpleNamespace(ready=True),
    )
    describe_error: ClassVar[Exception | None] = None
    last_index: ClassVar[FakeIndex | None] = None

    def __init__(self, *, api_key: str) -> None:
        assert api_key
        self.closed = False

    async def describe_index(self, index_name: str) -> Any:
        assert index_name == "stratix-knowledge"
        if type(self).describe_error is not None:
            raise type(self).describe_error
        return type(self).description

    def IndexAsyncio(self, *, host: str) -> FakeIndex:
        assert host
        index = FakeIndex()
        type(self).last_index = index
        return index

    async def close(self) -> None:
        self.closed = True


def configured_settings() -> Settings:
    return Settings(app_env="testing", _env_file=None).model_copy(
        update={
            "embeddings_enabled": True,
            "vector_search_enabled": True,
            "vector_db_provider": "pinecone",
            "pinecone_api_key": "diagnostic-secret-key",
            "pinecone_host": "https://private-index-host.example",
        }
    )


def reset_fake() -> None:
    FakeClient.description = SimpleNamespace(
        dimension=768,
        metric="cosine",
        status=SimpleNamespace(ready=True),
    )
    FakeClient.describe_error = None
    FakeClient.last_index = None
    FakeIndex.deleted_namespaces = []


def test_status_reports_safe_configuration_without_requiring_live_access(capsys) -> None:
    settings = Settings(app_env="testing", _env_file=None)

    assert cli(["status"], settings=settings, client_factory=FakeClient) == 0

    output = capsys.readouterr().out
    assert "enabled=false" in output
    assert "configured=false" in output
    assert "expected_dimensions=768" in output
    assert "expected_metric=cosine" in output
    assert "host_configured=false" in output


def test_validate_rejects_disabled_vector_search(capsys) -> None:
    settings = configured_settings().model_copy(update={"vector_search_enabled": False})

    assert cli(["validate"], settings=settings, client_factory=FakeClient) == 2

    assert "error_code=PINECONE_DISABLED" in capsys.readouterr().out


def test_validate_rejects_missing_key_and_host(capsys) -> None:
    missing_key = configured_settings().model_copy(update={"pinecone_api_key": None})
    missing_host = configured_settings().model_copy(update={"pinecone_host": None})

    assert cli(["validate"], settings=missing_key, client_factory=FakeClient) == 2
    assert "error_code=PINECONE_API_KEY_MISSING" in capsys.readouterr().out
    assert cli(["validate"], settings=missing_host, client_factory=FakeClient) == 2
    assert "error_code=PINECONE_HOST_MISSING" in capsys.readouterr().out


def test_validate_rejects_dimension_metric_readiness_and_unavailability(capsys) -> None:
    settings = configured_settings()
    cases = [
        (
            SimpleNamespace(
                dimension=1536,
                metric="cosine",
                status=SimpleNamespace(ready=True),
            ),
            None,
            "PINECONE_DIMENSION_MISMATCH",
        ),
        (
            SimpleNamespace(
                dimension=768,
                metric="dotproduct",
                status=SimpleNamespace(ready=True),
            ),
            None,
            "PINECONE_METRIC_MISMATCH",
        ),
        (
            SimpleNamespace(
                dimension=768,
                metric="cosine",
                status=SimpleNamespace(ready=False),
            ),
            None,
            "PINECONE_INDEX_NOT_READY",
        ),
        (
            FakeClient.description,
            RuntimeError("provider body must stay hidden"),
            "PINECONE_UNAVAILABLE",
        ),
    ]
    for description, error, expected in cases:
        reset_fake()
        FakeClient.description = description
        FakeClient.describe_error = error
        assert cli(["validate"], settings=settings, client_factory=FakeClient) == 2
        output = capsys.readouterr().out
        assert f"error_code={expected}" in output
        assert "provider body" not in output


def test_successful_validation_is_redacted(capsys) -> None:
    reset_fake()
    settings = configured_settings()

    assert cli(["validate"], settings=settings, client_factory=FakeClient) == 0

    output = capsys.readouterr().out
    assert "validation=passed" in output
    assert "dimensions=768" in output
    assert "metric=cosine" in output
    assert settings.pinecone_api_key not in output
    assert settings.pinecone_host not in output


def test_smoke_test_requires_guard_and_deletes_temporary_namespace(capsys) -> None:
    reset_fake()
    settings = configured_settings()

    assert cli(["smoke-test"], settings=settings, client_factory=FakeClient) == 2
    assert "error_code=PINECONE_SMOKE_GUARD_REQUIRED" in capsys.readouterr().out
    assert (
        cli(
            ["smoke-test", "--confirm-temporary-write"],
            settings=settings,
            client_factory=FakeClient,
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "smoke_test=passed" in output
    assert "temporary_namespace_deleted=true" in output
    assert len(FakeIndex.deleted_namespaces) == 1
    assert FakeIndex.deleted_namespaces[0].startswith("stratix-diagnostic-smoke-")
    assert not FakeIndex.deleted_namespaces[0].startswith(settings.pinecone_namespace_prefix)
    assert settings.pinecone_api_key not in output
    assert settings.pinecone_host not in output
