"""Storage backend interface — the seam that makes local-disk ↔ R2 a one-line swap.

The pipeline only ever talks to this; it never knows whether bytes live on disk or in R2.
"""
from __future__ import annotations

from typing import BinaryIO, Protocol


class StorageBackend(Protocol):
    def save_stream(self, key: str, stream: BinaryIO) -> int:
        """Persist a readable stream under `key`. Returns bytes written."""
        ...

    def save_file(self, key: str, local_path: str) -> None:
        """Upload a local file to `key`."""

    def download(self, key: str, local_path: str) -> None:
        """Fetch `key` to a local path (for ffmpeg/AI which need local bytes)."""

    def presigned_get(self, key: str, expires: int) -> str:
        """A short-lived URL the client can download directly from."""

    def delete_prefix(self, prefix: str) -> None:
        """Delete everything under a prefix (job-scoped cleanup)."""

    def exists(self, key: str) -> bool:
        ...
