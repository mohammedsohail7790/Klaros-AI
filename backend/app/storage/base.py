"""section 17/46: object storage abstraction.

Business logic (job attachment tools) calls this interface, never a
filesystem or S3 SDK directly — the same provider-adapter pattern as
app/integrations/ and app/calendar/.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StoredObject:
    storage_key: str
    size_bytes: int
    provider: str


class ObjectStorageProvider(ABC):
    provider_name: str

    @abstractmethod
    async def put(
        self, tenant_id: uuid.UUID, filename: str, content: bytes, content_type: str
    ) -> StoredObject: ...

    @abstractmethod
    async def get(self, tenant_id: uuid.UUID, storage_key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, tenant_id: uuid.UUID, storage_key: str) -> None: ...


class NotConnectedObjectStorageError(Exception):
    pass


class NotConnectedObjectStorageAdapter(ObjectStorageProvider):
    """Real S3(-compatible) storage — no endpoint/credentials configured, so
    every method fails loudly rather than pretending a file was stored."""

    provider_name = "s3_not_connected"

    async def put(self, tenant_id, filename, content, content_type) -> StoredObject:
        raise NotConnectedObjectStorageError(
            "External object storage is NOT_CONNECTED (OBJECT_STORAGE_ENDPOINT not configured)"
        )

    async def get(self, tenant_id, storage_key) -> bytes:
        raise NotConnectedObjectStorageError("External object storage is NOT_CONNECTED")

    async def delete(self, tenant_id, storage_key) -> None:
        raise NotConnectedObjectStorageError("External object storage is NOT_CONNECTED")
