"""INTERNAL LOCAL STORAGE (section 17/46).

A real, working object store on local disk — not a mock. Stands in for a
real S3-compatible bucket until `OBJECT_STORAGE_ENDPOINT` is configured
(see app/storage/factory.py). Every file is written under
`{root}/{tenant_id}/{uuid}_{sanitized_filename}` — the tenant_id segment is
never taken from client input for reads, only used to scope where writes
land, so one tenant can never read another's key even if it guessed one.
"""

import re
import uuid
from pathlib import Path

from app.storage.base import ObjectStorageProvider, StoredObject

MAX_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "application/pdf",
    "text/plain",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/webm",
}


class UnsupportedFileError(Exception):
    pass


def _sanitize_filename(filename: str) -> str:
    base = Path(filename).name  # strips any directory components
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:200] or "file"


class LocalFilesystemStorageAdapter(ObjectStorageProvider):
    provider_name = "internal_local_storage"

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def _tenant_dir(self, tenant_id: uuid.UUID) -> Path:
        d = self._root / str(tenant_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def put(
        self, tenant_id: uuid.UUID, filename: str, content: bytes, content_type: str
    ) -> StoredObject:
        if len(content) > MAX_SIZE_BYTES:
            raise UnsupportedFileError(f"File exceeds {MAX_SIZE_BYTES} byte limit")
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedFileError(f"Unsupported content type: {content_type}")

        safe_name = _sanitize_filename(filename)
        key = f"{uuid.uuid4()}_{safe_name}"
        path = self._tenant_dir(tenant_id) / key
        path.write_bytes(content)
        return StoredObject(storage_key=key, size_bytes=len(content), provider=self.provider_name)

    async def get(self, tenant_id: uuid.UUID, storage_key: str) -> bytes:
        path = self._resolve(tenant_id, storage_key)
        return path.read_bytes()

    async def delete(self, tenant_id: uuid.UUID, storage_key: str) -> None:
        path = self._resolve(tenant_id, storage_key)
        path.unlink(missing_ok=True)

    def _resolve(self, tenant_id: uuid.UUID, storage_key: str) -> Path:
        if "/" in storage_key or ".." in storage_key:
            raise ValueError("Invalid storage key")
        return self._tenant_dir(tenant_id) / storage_key
