"""Local-disk storage backend (dev, tests, and the one-command local fallback).

Mirrors the R2 layout under DATA_DIR/objects/. `presigned_get` returns an app URL the API
serves itself (see api/routes.py download handler).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import BinaryIO

from app.config import settings

CHUNK = 1024 * 1024


class LocalStorage:
    def __init__(self) -> None:
        self.root = Path(settings.data_dir) / "objects"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("Invalid storage key (path traversal).")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def save_stream(self, key: str, stream: BinaryIO) -> int:
        total = 0
        with open(self._path(key), "wb") as f:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        return total

    def save_file(self, key: str, local_path: str) -> None:
        shutil.copyfile(local_path, self._path(key))

    def download(self, key: str, local_path: str) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), local_path)

    def presigned_get(self, key: str, expires: int) -> str:
        # Served by the app itself for the local backend.
        return f"/api/files/{key}"

    def delete_prefix(self, prefix: str) -> None:
        target = (self.root / prefix).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            return
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def local_path_for(self, key: str) -> str:
        return str(self._path(key))
