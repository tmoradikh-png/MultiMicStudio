"""Storage abstraction.

MVP uses LocalStorage (files on disk, served by the API). The interface is identical
to what an S3-backed implementation needs, so switching to object storage later only
requires implementing `S3Storage` and flipping STORAGE_BACKEND=s3.
"""
from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings

settings = get_settings()


class Storage(ABC):
    @abstractmethod
    def save(self, key: str, fileobj: BinaryIO) -> str:
        """Persist a file under `key`; return a URL/path usable to fetch it later."""

    @abstractmethod
    def path(self, key: str) -> str:
        """Return a local filesystem path for processing (download if remote)."""

    @abstractmethod
    def public_url(self, key: str) -> str:
        """Return a URL the client can use to fetch the file."""

    @abstractmethod
    def delete(self, key: str) -> None: ...


class LocalStorage(Storage):
    def __init__(self, base_dir: str) -> None:
        self.base = Path(base_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def _full(self, key: str) -> Path:
        full = (self.base / key).resolve()
        # Prevent path traversal outside the storage root.
        if not str(full).startswith(str(self.base)):
            raise ValueError("Invalid storage key")
        return full

    def save(self, key: str, fileobj: BinaryIO) -> str:
        full = self._full(key)
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "wb") as out:
            shutil.copyfileobj(fileobj, out)
        return self.public_url(key)

    def path(self, key: str) -> str:
        return str(self._full(key))

    def public_url(self, key: str) -> str:
        # Served by the /files route in main.py.
        return f"/files/{key}"

    def delete(self, key: str) -> None:
        full = self._full(key)
        if full.exists():
            os.remove(full)


def get_storage() -> Storage:
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_local_dir)
    # Placeholder seam for the full product:
    # if settings.storage_backend == "s3":
    #     return S3Storage(...)
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")


def key_to_relpath(file_url: str) -> str:
    """Convert a stored public_url back into the storage key."""
    return file_url.removeprefix("/files/")
