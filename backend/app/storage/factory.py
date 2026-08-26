from functools import lru_cache

from app.core.config import get_settings
from app.storage.base import NotConnectedObjectStorageAdapter, ObjectStorageProvider
from app.storage.local_adapter import LocalFilesystemStorageAdapter


@lru_cache
def get_object_storage() -> ObjectStorageProvider:
    settings = get_settings()
    if settings.OBJECT_STORAGE_ENDPOINT:
        # Real S3-compatible client wiring is not implemented yet — no
        # credentials exist to test against, so this stays NOT_CONNECTED
        # rather than pretending an untested client works.
        return NotConnectedObjectStorageAdapter()
    return LocalFilesystemStorageAdapter(settings.STORAGE_LOCAL_ROOT)
