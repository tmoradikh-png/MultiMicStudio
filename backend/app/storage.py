"""Storage abstraction.

The MVP uses LocalStorage (files on disk, served by the API). The interface is
identical to what an S3-backed implementation needs, so switching to object
storage only requires `storage_backend=s3` plus the S3 settings — no call-site
changes. Everything here is backward compatible: with the default settings the
behaviour is exactly the MVP's (relative ``/files/<key>`` URLs, open static mount).

Hosting prep added here (all opt-in, default off):
  * absolute URLs via ``PUBLIC_BASE_URL`` so a real domain/CDN works,
  * HMAC-signed, time-limited file URLs (``FILE_URL_SIGNING=true``) so audio is
    not world-enumerable on a hosted backend,
  * an S3/object-storage backend (lazy boto3 import; only used when selected).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlencode

from app.config import get_settings

settings = get_settings()


# --- Signed-URL helpers ----------------------------------------------------
# A signature binds (key, expiry) with HMAC-SHA256 so a link cannot be altered
# or reused past its TTL. Verification is constant-time. These are pure functions
# so they are trivially unit-testable and shared by every storage backend.

def _sign(key: str, expires: int) -> str:
    msg = f"{key}:{expires}".encode("utf-8")
    return hmac.new(
        settings.signing_key.encode("utf-8"), msg, hashlib.sha256
    ).hexdigest()


def make_signed_query(key: str, ttl_seconds: int | None = None) -> str:
    """Return the ``exp=..&sig=..`` query string for a key."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.file_url_ttl_seconds
    expires = int(time.time()) + ttl
    return urlencode({"exp": expires, "sig": _sign(key, expires)})


def verify_signature(key: str, expires: str | int | None, sig: str | None) -> bool:
    """True if ``sig`` matches ``key``+``expires`` and the link has not expired."""
    if expires is None or sig is None:
        return False
    try:
        exp_int = int(expires)
    except (TypeError, ValueError):
        return False
    if exp_int < int(time.time()):
        return False
    return hmac.compare_digest(_sign(key, exp_int), sig)


def _with_base(path: str) -> str:
    """Prefix a relative ``/files/..`` path with PUBLIC_BASE_URL when configured."""
    base = settings.public_base_url.rstrip("/")
    return f"{base}{path}" if base else path


class Storage(ABC):
    @abstractmethod
    def save(self, key: str, fileobj: BinaryIO) -> str:
        """Persist a file under `key`; return a URL/path usable to fetch it later."""

    @abstractmethod
    def path(self, key: str) -> str:
        """Return a local filesystem path for processing (download if remote)."""

    @abstractmethod
    def public_url(self, key: str) -> str:
        """Return a URL the client can use to fetch the file (unsigned)."""

    @abstractmethod
    def signed_url(self, key: str, ttl_seconds: int | None = None) -> str:
        """Return a fetch URL that is time-limited when signing/presigning is on.

        When signing is off this is identical to ``public_url`` so callers can use
        it unconditionally and get secure links automatically once hosting enables
        signing — no further code changes needed.
        """

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
        return _with_base(f"/files/{key}")

    def signed_url(self, key: str, ttl_seconds: int | None = None) -> str:
        if not settings.file_url_signing:
            return self.public_url(key)
        return _with_base(f"/files/{key}?{make_signed_query(key, ttl_seconds)}")

    def delete(self, key: str) -> None:
        full = self._full(key)
        if full.exists():
            os.remove(full)


class S3Storage(Storage):
    """Object-storage backend for hosted deployments (S3 / R2 / Spaces / MinIO).

    boto3 is imported lazily so the local MVP never needs it installed. Files are
    fetched to a temp path for the offline processing pipeline (which works on
    local WAVs); uploads stream straight to the bucket.
    """

    def __init__(self) -> None:
        import boto3  # lazy: only required when storage_backend == "s3"

        self._tmp_dir = Path(settings.storage_local_dir).resolve() / "_s3cache"
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self.bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            region_name=settings.s3_region or None,
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
        )

    def save(self, key: str, fileobj: BinaryIO) -> str:
        self._client.upload_fileobj(fileobj, self.bucket, key)
        return self.public_url(key)

    def path(self, key: str) -> str:
        local = (self._tmp_dir / key).resolve()
        local.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(local))
        return str(local)

    def public_url(self, key: str) -> str:
        base = settings.s3_public_base_url.rstrip("/")
        if base:
            return f"{base}/{key}"
        # No public CDN base => fall back to a presigned GET.
        return self.signed_url(key)

    def signed_url(self, key: str, ttl_seconds: int | None = None) -> str:
        ttl = ttl_seconds if ttl_seconds is not None else settings.file_url_ttl_seconds
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl,
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> Storage:
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_local_dir)
    if settings.storage_backend == "s3":
        return S3Storage()
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")


def key_to_relpath(file_url: str) -> str:
    """Convert a stored public_url back into the storage key.

    Tolerates absolute URLs (PUBLIC_BASE_URL / CDN) and signed-link query strings,
    so a stored value round-trips back to its key regardless of how it was built.
    """
    url = file_url.split("?", 1)[0]  # drop any signature query
    marker = "/files/"
    idx = url.find(marker)
    if idx != -1:
        return url[idx + len(marker):]
    return url.removeprefix("/files/")
