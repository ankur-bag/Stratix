import asyncio
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class VectorStoreError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class VectorRecord:
    id: str
    values: list[float]
    metadata: dict[str, str | int | bool]


@dataclass(frozen=True)
class VectorMatch:
    id: str
    score: float
    metadata: dict[str, str | int | bool]


class VectorStore(Protocol):
    enabled: bool

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def upsert(self, namespace: str, records: Sequence[VectorRecord]) -> int: ...
    async def query(
        self,
        namespace: str,
        vector: Sequence[float],
        *,
        top_k: int,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> list[VectorMatch]: ...
    async def delete(
        self,
        namespace: str,
        *,
        ids: Sequence[str] | None = None,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> None: ...


class DisabledVectorStore:
    enabled = False

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def upsert(self, namespace: str, records: Sequence[VectorRecord]) -> int:
        del namespace, records
        return 0

    async def query(
        self,
        namespace: str,
        vector: Sequence[float],
        *,
        top_k: int,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> list[VectorMatch]:
        del namespace, vector, top_k, metadata_filter
        return []

    async def delete(
        self,
        namespace: str,
        *,
        ids: Sequence[str] | None = None,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> None:
        del namespace, ids, metadata_filter


class PineconeVectorStore:
    enabled = True

    def __init__(
        self,
        *,
        api_key: str,
        host: str,
        index_name: str,
        dimensions: int,
        metric: str,
        upsert_batch_size: int,
        maximum_concurrency: int,
        maximum_retries: int,
        retry_base_seconds: float,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._host = host
        self._index_name = index_name
        self._dimensions = dimensions
        self._metric = metric
        self._batch_size = upsert_batch_size
        self._semaphore = asyncio.Semaphore(maximum_concurrency)
        self._maximum_retries = maximum_retries
        self._retry_base_seconds = retry_base_seconds
        self._client = client
        self._index: Any | None = None

    async def start(self) -> None:
        if self._client is None:
            from pinecone import PineconeAsyncio

            self._client = PineconeAsyncio(api_key=self._api_key)
        description = await self._client.describe_index(self._index_name)
        dimension = self._value(description, "dimension")
        metric = self._value(description, "metric")
        status = self._value(description, "status") or {}
        ready = status.get("ready") if isinstance(status, dict) else getattr(status, "ready", False)
        if dimension != self._dimensions:
            raise VectorStoreError(
                "VECTOR_INDEX_DIMENSION_MISMATCH",
                "The configured vector index has an incompatible dimension.",
            )
        if metric != self._metric:
            raise VectorStoreError(
                "VECTOR_INDEX_METRIC_MISMATCH",
                "The configured vector index has an incompatible metric.",
            )
        if not ready:
            raise VectorStoreError(
                "VECTOR_INDEX_NOT_READY", "The configured vector index is not ready."
            )
        self._index = self._client.IndexAsyncio(host=self._host)

    async def close(self) -> None:
        if self._index is not None and hasattr(self._index, "close"):
            await self._index.close()
        if self._client is not None and hasattr(self._client, "close"):
            await self._client.close()

    async def upsert(self, namespace: str, records: Sequence[VectorRecord]) -> int:
        batches = [
            records[index : index + self._batch_size]
            for index in range(0, len(records), self._batch_size)
        ]

        async def send(batch: Sequence[VectorRecord]) -> int:
            response = await self._retry(
                lambda: self._required_index().upsert(
                    namespace=namespace,
                    vectors=[
                        {
                            "id": record.id,
                            "values": record.values,
                            "metadata": record.metadata,
                        }
                        for record in batch
                    ],
                )
            )
            acknowledged = self._value(response, "upserted_count")
            if acknowledged is not None and int(acknowledged) != len(batch):
                raise VectorStoreError(
                    "VECTOR_UPSERT_PARTIAL",
                    "The vector store did not acknowledge the complete batch.",
                )
            return len(batch)

        return sum(await asyncio.gather(*(send(batch) for batch in batches)))

    async def query(
        self,
        namespace: str,
        vector: Sequence[float],
        *,
        top_k: int,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> list[VectorMatch]:
        response = await self._retry(
            lambda: self._required_index().query(
                namespace=namespace,
                vector=list(vector),
                top_k=top_k,
                filter=dict(metadata_filter or {}),
                include_values=False,
                include_metadata=True,
            )
        )
        matches = self._value(response, "matches") or []
        return [
            VectorMatch(
                id=str(self._value(match, "id")),
                score=float(self._value(match, "score") or 0),
                metadata=dict(self._value(match, "metadata") or {}),
            )
            for match in matches
        ]

    async def delete(
        self,
        namespace: str,
        *,
        ids: Sequence[str] | None = None,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> None:
        if not ids and not metadata_filter:
            return
        await self._retry(
            lambda: self._required_index().delete(
                namespace=namespace,
                ids=list(ids) if ids else None,
                filter=dict(metadata_filter) if metadata_filter else None,
            )
        )

    async def _retry(self, operation: Any) -> Any:
        async with self._semaphore:
            for attempt in range(self._maximum_retries + 1):
                try:
                    return await operation()
                except VectorStoreError:
                    raise
                except Exception as exc:
                    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
                    if attempt >= self._maximum_retries or status not in {
                        408,
                        409,
                        429,
                        500,
                        502,
                        503,
                        504,
                    }:
                        raise VectorStoreError(
                            "VECTOR_STORE_UNAVAILABLE",
                            "The vector store is temporarily unavailable.",
                        ) from exc
                    delay = self._retry_base_seconds * (2**attempt)
                    await asyncio.sleep(delay + random.uniform(0, delay * 0.1))
        raise AssertionError("unreachable")

    def _required_index(self) -> Any:
        if self._index is None:
            raise VectorStoreError(
                "VECTOR_STORE_UNAVAILABLE", "The vector store is not initialized."
            )
        return self._index

    @staticmethod
    def _value(value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)


class FakeVectorStore:
    enabled = True

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions
        self.namespaces: dict[str, dict[str, VectorRecord]] = {}
        self.queries: list[dict[str, Any]] = []
        self.transient_failures = 0
        self.partial_upsert = False

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def upsert(self, namespace: str, records: Sequence[VectorRecord]) -> int:
        if self.transient_failures:
            self.transient_failures -= 1
            raise VectorStoreError(
                "VECTOR_STORE_UNAVAILABLE", "The fake vector store is unavailable."
            )
        if any(len(record.values) != self.dimensions for record in records):
            raise VectorStoreError("VECTOR_INDEX_DIMENSION_MISMATCH", "Vector dimension mismatch.")
        acknowledged = len(records) - 1 if self.partial_upsert and records else len(records)
        target = self.namespaces.setdefault(namespace, {})
        for record in records[:acknowledged]:
            target[record.id] = record
        if acknowledged != len(records):
            raise VectorStoreError("VECTOR_UPSERT_PARTIAL", "Partial vector upsert.")
        return acknowledged

    async def query(
        self,
        namespace: str,
        vector: Sequence[float],
        *,
        top_k: int,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> list[VectorMatch]:
        if len(vector) != self.dimensions:
            raise VectorStoreError("VECTOR_INDEX_DIMENSION_MISMATCH", "Vector dimension mismatch.")
        self.queries.append(
            {
                "namespace": namespace,
                "top_k": top_k,
                "metadata_filter": dict(metadata_filter or {}),
            }
        )
        matches = [
            VectorMatch(record.id, self._cosine(vector, record.values), record.metadata)
            for record in self.namespaces.get(namespace, {}).values()
            if self._matches(record.metadata, metadata_filter or {})
        ]
        return sorted(matches, key=lambda item: (-item.score, item.id))[:top_k]

    async def delete(
        self,
        namespace: str,
        *,
        ids: Sequence[str] | None = None,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> None:
        target = self.namespaces.get(namespace, {})
        if ids:
            for vector_id in ids:
                target.pop(vector_id, None)
        if metadata_filter:
            for vector_id, record in list(target.items()):
                if self._matches(record.metadata, metadata_filter):
                    target.pop(vector_id, None)

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
            sum(value * value for value in right)
        )
        return (
            sum(a * b for a, b in zip(left, right, strict=True)) / denominator if denominator else 0
        )

    @staticmethod
    def _matches(metadata: Mapping[str, str | int | bool], requested: Mapping[str, Any]) -> bool:
        for key, expected in requested.items():
            if key == "$and":
                return all(FakeVectorStore._matches(metadata, item) for item in expected)
            if key == "$or":
                return any(FakeVectorStore._matches(metadata, item) for item in expected)
            if isinstance(expected, Mapping):
                if "$eq" in expected and metadata.get(key) != expected["$eq"]:
                    return False
                if "$in" in expected and metadata.get(key) not in expected["$in"]:
                    return False
                if "$ne" in expected and metadata.get(key) == expected["$ne"]:
                    return False
                actual = metadata.get(key)
                if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                    return False
                if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                    return False
                if "$gt" in expected and (actual is None or actual <= expected["$gt"]):
                    return False
                if "$lt" in expected and (actual is None or actual >= expected["$lt"]):
                    return False
            elif metadata.get(key) != expected:
                return False
        return True
