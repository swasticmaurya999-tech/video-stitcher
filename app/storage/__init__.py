"""Storage backend factory — local disk or R2, chosen by config."""
from __future__ import annotations

from app.config import settings
from app.storage.base import StorageBackend


def get_storage() -> StorageBackend:
    if settings.storage_backend == "r2":
        from app.storage.r2 import R2Storage

        return R2Storage()
    from app.storage.local import LocalStorage

    return LocalStorage()


storage: StorageBackend = get_storage()
