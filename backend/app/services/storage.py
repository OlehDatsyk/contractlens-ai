"""Private document storage backends.

Files are always stored under a random, server-generated key - never under a user-supplied filename - and are
only ever served through authorised API endpoints. No backend produces public URLs.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete, select

from app.core.config import get_settings

log = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """Raised when the storage backend cannot complete an operation."""


class Storage(Protocol):
    name: str

    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def healthy(self) -> bool: ...


def new_key(prefix: str, workspace_id: object) -> str:
    return f"{prefix}/{workspace_id}/{secrets.token_hex(16)}"


def _safe_key(key: str) -> str:
    parts = key.split("/")
    if not key or key.startswith("/") or any(p in ("", ".", "..") for p in parts) or "\\" in key:
        raise StorageError("Invalid storage key")
    if not all(c.isalnum() or c in "-_/" for c in key):
        raise StorageError("Invalid storage key")
    return key


class LocalStorage:
    name = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _safe_key(key)).resolve()
        if self.root not in path.parents:
            raise StorageError("Invalid storage key")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError("Could not write file to local storage") from exc

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("Stored file is missing") from exc
        except OSError as exc:
            raise StorageError("Could not read stored file") from exc

    def delete(self, key: str) -> None:
        path = self._path(key)
        if not path.exists():
            return
        try:
            # Best-effort overwrite before unlinking. On SSDs / copy-on-write filesystems this does not guarantee
            # physical erasure; full-disk encryption is the recommended control (see SECURITY.md).
            size = path.stat().st_size
            with open(path, "r+b") as fh:
                fh.write(secrets.token_bytes(min(size, 32 * 1024 * 1024)))
                fh.flush()
                os.fsync(fh.fileno())
            path.unlink()
        except OSError as exc:
            raise StorageError("Could not delete stored file") from exc

    def healthy(self) -> bool:
        return self.root.exists() and os.access(self.root, os.W_OK)


class S3Storage:
    """S3-compatible private object storage (AWS S3, Cloudflare R2, Backblaze B2, MinIO...)."""

    name = "s3"

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = get_settings()
        self.bucket = s.object_storage_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.object_storage_endpoint or None,
            aws_access_key_id=s.object_storage_access_key,
            aws_secret_access_key=s.object_storage_secret_key,
            region_name=s.object_storage_region or None,
            config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        kwargs: dict[str, object] = {
            "Bucket": self.bucket, "Key": _safe_key(key), "Body": data, "ContentType": content_type,
        }
        if not get_settings().object_storage_endpoint:  # AWS S3: request server-side encryption explicitly
            kwargs["ServerSideEncryption"] = "AES256"
        try:
            self.client.put_object(**kwargs)
        except (BotoCoreError, ClientError) as exc:
            log.error("Object storage upload failed: %s", type(exc).__name__)
            raise StorageError("Object storage upload failed") from exc

    def get(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))
            return bytes(obj["Body"].read())
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Object storage download failed") from exc

    def delete(self, key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self.client.delete_object(Bucket=self.bucket, Key=_safe_key(key))
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Object storage delete failed") from exc

    def healthy(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except Exception:  # noqa: BLE001 - health probe
            return False


class DatabaseStorage:
    """Stores bytes in PostgreSQL (``stored_blobs``). Persistent and private with no extra infrastructure;
    suitable for small and medium deployments. Each operation uses its own short transaction."""

    name = "database"

    def put(self, key: str, data: bytes, content_type: str) -> None:
        from app.core.db import session_scope
        from app.models import StoredBlob

        try:
            with session_scope() as db:
                db.merge(StoredBlob(key=_safe_key(key), data=data))
        except Exception as exc:
            raise StorageError("Database storage write failed") from exc

    def get(self, key: str) -> bytes:
        from app.core.db import session_scope
        from app.models import StoredBlob

        with session_scope() as db:
            data = db.scalar(select(StoredBlob.data).where(StoredBlob.key == _safe_key(key)))
        if data is None:
            raise StorageError("Stored file is missing")
        return bytes(data)

    def delete(self, key: str) -> None:
        from app.core.db import session_scope
        from app.models import StoredBlob

        with session_scope() as db:
            db.execute(delete(StoredBlob).where(StoredBlob.key == _safe_key(key)))

    def healthy(self) -> bool:
        return True


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        backend = get_settings().storage_backend
        if backend == "s3":
            _storage = S3Storage()
        elif backend == "database":
            _storage = DatabaseStorage()
        else:
            _storage = LocalStorage(get_settings().local_storage_path)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
