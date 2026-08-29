"""Storage abstraction for uploaded files — Task 5.1.

A tiny backend-agnostic blob store. Two implementations:

- ``LocalDiskStorage`` (default): writes ``backend/uploads/<key>``. ``url()``
  returns ``None`` so blobs are streamed back through the authenticated API
  (``GET /api/files/{id}``) — never a public link. Keys are confined to the
  uploads dir (path-traversal is rejected).
- ``S3Storage``: stores objects in an S3 bucket and ``url()`` returns a short
  lived presigned GET. ``boto3`` is imported LAZILY (inside ``__init__`` and the
  methods) so the app boots and local mode works with boto3 absent from the venv.

``get_storage()`` picks the backend from ``STORAGE_BACKEND`` (``local`` | ``s3``,
default ``local``). This is a regulated clinical app — uploaded files may carry
PHI, so nothing here bypasses the API's scope checks: local ``url()`` is always
``None`` and the S3 presigned URL is short-lived and generated per request.
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Optional, Protocol, Tuple, runtime_checkable

from starlette.concurrency import run_in_threadpool

# backend/uploads/ — sibling of this module.
UPLOADS_DIR = Path(__file__).resolve().parent / 'uploads'


@runtime_checkable
class Storage(Protocol):
    async def save(self, key: str, data: bytes, content_type: str) -> str: ...
    async def open(self, key: str) -> Tuple[bytes, str]: ...
    async def delete(self, key: str) -> None: ...
    def url(self, key: str) -> Optional[str]: ...   # presigned for S3, None for local


class LocalDiskStorage:
    """Blobs on local disk under ``backend/uploads/``. Served via the API."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base = Path(base_dir) if base_dir else UPLOADS_DIR
        self.base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        """Map a key to an absolute path *inside* the uploads dir, rejecting any
        key that would escape it (``..`` segments, absolute paths, drive letters,
        symlink-ish tricks). Fail-closed: raises ValueError on traversal."""
        if not key or key in ('.', '..'):
            raise ValueError('invalid storage key')
        # Reject absolute paths and explicit parent traversal outright.
        p = Path(key)
        if p.is_absolute() or p.drive or '..' in p.parts:
            raise ValueError('invalid storage key: path traversal')
        target = (self.base / p).resolve()
        base = self.base.resolve()
        if target != base and base not in target.parents:
            raise ValueError('invalid storage key: escapes uploads dir')
        return target

    async def save(self, key: str, data: bytes, content_type: str) -> str:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)

        def _write():
            with open(target, 'wb') as fh:
                fh.write(data)
        await run_in_threadpool(_write)
        return key

    async def open(self, key: str) -> Tuple[bytes, str]:
        target = self._resolve(key)

        def _read() -> bytes:
            with open(target, 'rb') as fh:
                return fh.read()
        data = await run_in_threadpool(_read)
        # Content-type is authoritative on the db doc; disk keys carry no
        # extension, so guess (falls back to octet-stream).
        ct = mimetypes.guess_type(key)[0] or 'application/octet-stream'
        return data, ct

    async def delete(self, key: str) -> None:
        target = self._resolve(key)

        def _rm():
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        await run_in_threadpool(_rm)

    def url(self, key: str) -> Optional[str]:
        return None   # local blobs are streamed through the authenticated API


class S3Storage:
    """S3-backed blob store. ``boto3`` is imported lazily so its absence never
    breaks local mode / app boot."""

    PRESIGN_TTL = 900   # 15 minutes

    def __init__(self, bucket: Optional[str] = None, region: Optional[str] = None):
        import boto3   # lazy: only when S3 is actually selected
        self.bucket = bucket or os.environ.get('S3_BUCKET')
        if not self.bucket:
            raise RuntimeError('S3_BUCKET is not configured for STORAGE_BACKEND=s3')
        self.region = region or os.environ.get('AWS_REGION') or 'us-east-1'
        self._client = boto3.client('s3', region_name=self.region)

    async def save(self, key: str, data: bytes, content_type: str) -> str:
        await run_in_threadpool(
            lambda: self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data,
                ContentType=content_type or 'application/octet-stream'))
        return key

    async def open(self, key: str) -> Tuple[bytes, str]:
        def _get():
            obj = self._client.get_object(Bucket=self.bucket, Key=key)
            return obj['Body'].read(), obj.get('ContentType', 'application/octet-stream')
        return await run_in_threadpool(_get)

    async def delete(self, key: str) -> None:
        await run_in_threadpool(
            lambda: self._client.delete_object(Bucket=self.bucket, Key=key))

    def url(self, key: str) -> Optional[str]:
        return self._client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket, 'Key': key},
            ExpiresIn=self.PRESIGN_TTL)


def get_storage() -> Storage:
    """Pick the storage backend from ``STORAGE_BACKEND`` (default ``local``)."""
    backend = (os.environ.get('STORAGE_BACKEND') or 'local').strip().lower()
    if backend == 's3':
        return S3Storage()
    return LocalDiskStorage()
