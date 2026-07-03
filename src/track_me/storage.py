"""Object-store abstraction for ingest sources.

Ingest reads photos + sidecars through this interface instead of the filesystem,
so the same pipeline works over a local directory, an S3 bucket, or (later) GCS.
Keys are '/'-separated strings; a key's "directory" is everything before the last
'/'. Sizes come from the listing, so no per-object stat is ever needed.

    store, prefix = from_uri("s3://my-bucket/Takeout")
    for obj in store.list(prefix):
        data = store.read(obj.key)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    last_modified: datetime | None = None


@runtime_checkable
class ObjectStore(Protocol):
    """Minimal read-only object store: list, read whole, read a byte range."""

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]: ...

    def read(self, key: str) -> bytes: ...

    def read_range(self, key: str, start: int, length: int) -> bytes: ...


class LocalStore:
    """Objects under a local root directory. Keys are POSIX paths relative to it."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _abs(self, key: str) -> Path:
        return self.root / key

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        base = self.root / prefix if prefix else self.root
        if base.is_file():  # a single-file "prefix"
            st = base.stat()
            yield ObjectInfo(base.relative_to(self.root).as_posix(), st.st_size)
            return
        for dirpath, _dirs, filenames in os.walk(base):
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                key = p.relative_to(self.root).as_posix()
                yield ObjectInfo(key, st.st_size, _mtime(st.st_mtime))

    def read(self, key: str) -> bytes:
        return self._abs(key).read_bytes()

    def read_range(self, key: str, start: int, length: int) -> bytes:
        with open(self._abs(key), "rb") as fh:
            fh.seek(start)
            return fh.read(length)


class S3Store:
    """Objects in an S3 bucket via boto3 (standard credential chain)."""

    def __init__(self, bucket: str, client=None):
        self.bucket = bucket
        if client is None:
            import boto3  # lazy: only needed when actually using S3

            client = boto3.client("s3")
        self.client = client

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith("/"):  # skip directory placeholders
                    continue
                yield ObjectInfo(obj["Key"], obj["Size"], obj.get("LastModified"))

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def read_range(self, key: str, start: int, length: int) -> bytes:
        rng = f"bytes={start}-{start + length - 1}"
        return self.client.get_object(Bucket=self.bucket, Key=key, Range=rng)["Body"].read()


def _mtime(ts: float) -> datetime | None:
    from datetime import UTC

    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def from_uri(uri: str) -> tuple[ObjectStore, str]:
    """Resolve a source URI to (store, prefix).

    - ``s3://bucket/prefix``       -> (S3Store(bucket), 'prefix')
    - ``gs://bucket/prefix``       -> (not implemented yet)
    - a local path (``/x`` etc.)   -> (LocalStore(path), '')
    """
    if uri.startswith("s3://"):
        rest = uri[len("s3://") :]
        bucket, _, prefix = rest.partition("/")
        return S3Store(bucket), prefix
    if uri.startswith("gs://"):
        raise NotImplementedError("gs:// (Google Cloud Storage) support not implemented yet")
    return LocalStore(uri), ""
