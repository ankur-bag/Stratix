import asyncio
import math
import random
from collections.abc import Sequence
from typing import Any, Protocol, cast

from google import genai
from google.genai import types


class EmbeddingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EmbeddingProvider(Protocol):
    enabled: bool
    dimensions: int
    version: str

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


class DisabledEmbeddingProvider:
    enabled = False

    def __init__(self, dimensions: int, version: str) -> None:
        self.dimensions = dimensions
        self.version = version

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        del texts
        raise EmbeddingError("EMBEDDING_DISABLED", "Embeddings are not enabled.")

    async def embed_query(self, text: str) -> list[float]:
        del text
        raise EmbeddingError("EMBEDDING_DISABLED", "Embeddings are not enabled.")


class GeminiEmbeddingProvider:
    enabled = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        document_task_type: str,
        query_task_type: str,
        batch_size: int,
        maximum_concurrency: int,
        maximum_retries: int,
        retry_base_seconds: float,
        version: str,
        client: Any | None = None,
    ) -> None:
        self._client = client or genai.Client(api_key=api_key)
        self._model = model
        self.dimensions = dimensions
        self._document_task_type = document_task_type
        self._query_task_type = query_task_type
        self._batch_size = batch_size
        self._semaphore = asyncio.Semaphore(maximum_concurrency)
        self._maximum_retries = maximum_retries
        self._retry_base_seconds = retry_base_seconds
        self.version = version

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        batches = [
            list(texts[index : index + self._batch_size])
            for index in range(0, len(texts), self._batch_size)
        ]
        results = await asyncio.gather(
            *(self._embed(batch, self._document_task_type) for batch in batches)
        )
        vectors = [vector for batch in results for vector in batch]
        self._validate(vectors, len(texts))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], self._query_task_type)
        self._validate(vectors, 1)
        return vectors[0]

    async def _embed(self, contents: list[str], task_type: str) -> list[list[float]]:
        async with self._semaphore:
            for attempt in range(self._maximum_retries + 1):
                try:
                    response = await self._client.aio.models.embed_content(
                        model=self._model,
                        contents=cast(Any, contents),
                        config=types.EmbedContentConfig(
                            task_type=task_type,
                            output_dimensionality=self.dimensions,
                        ),
                    )
                    return [list(item.values or []) for item in response.embeddings or []]
                except Exception as exc:
                    if attempt >= self._maximum_retries or not self._transient(exc):
                        raise EmbeddingError(
                            "EMBEDDING_PROVIDER_ERROR",
                            "The embedding provider could not process the document.",
                        ) from exc
                    delay = self._retry_base_seconds * (2**attempt)
                    await asyncio.sleep(delay + random.uniform(0, delay * 0.1))
        raise AssertionError("unreachable")

    def _validate(self, vectors: list[list[float]], expected_count: int) -> None:
        if len(vectors) != expected_count:
            raise EmbeddingError(
                "EMBEDDING_COUNT_MISMATCH",
                "The embedding provider returned an unexpected result count.",
            )
        if any(len(vector) != self.dimensions for vector in vectors):
            raise EmbeddingError(
                "EMBEDDING_DIMENSION_MISMATCH",
                "The embedding provider returned an unexpected vector dimension.",
            )
        if any(not math.isfinite(value) for vector in vectors for value in vector):
            raise EmbeddingError(
                "EMBEDDING_INVALID_VALUES",
                "The embedding provider returned invalid vector values.",
            )

    @staticmethod
    def _transient(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        return status in {408, 409, 429, 500, 502, 503, 504}


class FakeEmbeddingProvider:
    enabled = True

    def __init__(
        self,
        dimensions: int = 768,
        version: str = "fake-768-v1",
        *,
        fail: EmbeddingError | None = None,
    ) -> None:
        self.dimensions = dimensions
        self.version = version
        self.fail = fail
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if self.fail:
            raise self.fail
        self.document_calls.append(list(texts))
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        if self.fail:
            raise self.fail
        self.query_calls.append(text)
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for index, byte in enumerate(text.encode("utf-8")):
            vector[index % self.dimensions] += (byte + 1) / 256
        magnitude = math.sqrt(sum(value * value for value in vector)) or 1
        return [value / magnitude for value in vector]
