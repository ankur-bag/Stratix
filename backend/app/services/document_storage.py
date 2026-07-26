import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class StoredObject:
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class PresignedUpload:
    method: str
    url: str
    headers: dict[str, str]


class ObjectStorage(Protocol):
    direct_uploads: bool

    async def create_upload(self, key: str, content_type: str) -> PresignedUpload | None: ...
    async def put_stream(
        self, key: str, chunks: AsyncIterator[bytes], maximum_size: int
    ) -> StoredObject: ...
    async def read(self, key: str) -> bytes: ...
    async def stat(self, key: str) -> StoredObject | None: ...
    async def delete(self, key: str) -> None: ...


class StorageSizeExceededError(Exception):
    pass


def _safe_key(key: str) -> Path:
    candidate = Path(key)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Storage key must be relative and opaque.")
    return candidate


class LocalObjectStorage:
    """Private local storage for development. Files are never served statically."""

    direct_uploads = True

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    async def start(self) -> None:
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)

    async def create_upload(self, key: str, content_type: str) -> PresignedUpload | None:
        del key, content_type
        return None

    async def put_stream(
        self, key: str, chunks: AsyncIterator[bytes], maximum_size: int
    ) -> StoredObject:
        target = (self._root / _safe_key(key)).resolve()
        if self._root not in target.parents:
            raise ValueError("Storage key escaped the configured root.")
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.partial")
        size = 0
        digest = hashlib.sha256()
        try:
            with temporary.open("wb") as handle:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > maximum_size:
                        raise StorageSizeExceededError
                    digest.update(chunk)
                    await asyncio.to_thread(handle.write, chunk)
            await asyncio.to_thread(temporary.replace, target)
        except BaseException:
            if temporary.exists():
                await asyncio.to_thread(temporary.unlink)
            raise
        return StoredObject(size_bytes=size, sha256=digest.hexdigest())

    async def read(self, key: str) -> bytes:
        target = (self._root / _safe_key(key)).resolve()
        return await asyncio.to_thread(target.read_bytes)

    async def stat(self, key: str) -> StoredObject | None:
        target = (self._root / _safe_key(key)).resolve()
        if not target.is_file():
            return None
        data = await asyncio.to_thread(target.read_bytes)
        return StoredObject(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    async def delete(self, key: str) -> None:
        target = (self._root / _safe_key(key)).resolve()
        if target.is_file():
            await asyncio.to_thread(target.unlink)


class FakeObjectStorage:
    direct_uploads = True

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def start(self) -> None:
        return None

    async def create_upload(self, key: str, content_type: str) -> PresignedUpload | None:
        del key, content_type
        return None

    async def put_stream(
        self, key: str, chunks: AsyncIterator[bytes], maximum_size: int
    ) -> StoredObject:
        data = bytearray()
        async for chunk in chunks:
            data.extend(chunk)
            if len(data) > maximum_size:
                raise StorageSizeExceededError
        value = bytes(data)
        self.objects[key] = value
        return StoredObject(len(value), hashlib.sha256(value).hexdigest())

    async def read(self, key: str) -> bytes:
        return self.objects[key]

    async def stat(self, key: str) -> StoredObject | None:
        value = self.objects.get(key)
        if value is None:
            return None
        return StoredObject(len(value), hashlib.sha256(value).hexdigest())

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class S3ObjectStorage:
    """S3-compatible private storage using presigned PUT uploads."""

    direct_uploads = False

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint_url: str | None,
        upload_ttl_seconds: int,
        client: Any | None = None,
    ) -> None:
        if client is None:
            try:
                import boto3  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover - configuration guard
                raise RuntimeError("boto3 is required for S3 document storage") from exc
            client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)
        self._client = client
        self._bucket = bucket
        self._upload_ttl_seconds = upload_ttl_seconds

    async def start(self) -> None:
        return None

    async def create_upload(self, key: str, content_type: str) -> PresignedUpload:
        url = await asyncio.to_thread(
            self._client.generate_presigned_url,
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=self._upload_ttl_seconds,
        )
        return PresignedUpload("PUT", url, {"Content-Type": content_type})

    async def put_stream(
        self, key: str, chunks: AsyncIterator[bytes], maximum_size: int
    ) -> StoredObject:
        del key, chunks, maximum_size
        raise RuntimeError("S3 storage requires direct presigned upload")

    async def read(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)

    async def stat(self, key: str) -> StoredObject | None:
        try:
            response = await asyncio.to_thread(
                self._client.head_object, Bucket=self._bucket, Key=key
            )
        except Exception as exc:
            status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return None
            raise
        metadata = response.get("Metadata", {})
        digest = metadata.get("sha256", "")
        return StoredObject(int(response["ContentLength"]), digest)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
