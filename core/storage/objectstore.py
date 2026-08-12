"""S3/MinIO object store for uploaded frames (and later, clips).

Storage is a real cost: we keep the small extracted frames, never the multi-GB
source video (which in Phase 0 never leaves the browser anyway)."""
from __future__ import annotations

import time

import boto3
from botocore.client import Config

from core.config import get_settings


def _retry(fn, attempts: int = 4, base_delay: float = 0.5):
    """Retry a storage op on transient MinIO/network errors (IncompleteRead,
    connection resets) — these happen under load and almost always succeed on a
    retry. Re-raises the last error if all attempts fail."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - boto/urllib3 raise varied types
            last = exc
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    raise last


class ObjectStore:
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    def ensure_bucket(self) -> None:
        existing = {b["Name"] for b in self._client.list_buckets().get("Buckets", [])}
        if self._bucket not in existing:
            self._client.create_bucket(Bucket=self._bucket)

    def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> str:
        _retry(lambda: self._client.put_object(
            Bucket=self._bucket, Key=key, Body=data, ContentType=content_type))
        return key

    def get(self, key: str) -> bytes:
        def _do() -> bytes:
            obj = self._client.get_object(Bucket=self._bucket, Key=key)
            return obj["Body"].read()  # read() is where IncompleteRead surfaces
        return _retry(_do)

    def size(self, key: str) -> int:
        return _retry(lambda: self._client.head_object(Bucket=self._bucket, Key=key)["ContentLength"])

    def get_range(self, key: str, start: int, end: int) -> bytes:
        """Fetch bytes [start, end] inclusive — for HTTP range (video seeking)."""
        def _do() -> bytes:
            obj = self._client.get_object(
                Bucket=self._bucket, Key=key, Range=f"bytes={start}-{end}")
            return obj["Body"].read()
        return _retry(_do)

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._client.list_objects_v2(**kwargs)
            keys.extend(o["Key"] for o in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return sorted(keys)

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list(prefix)
        for k in keys:
            self._client.delete_object(Bucket=self._bucket, Key=k)
        return len(keys)


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = ObjectStore()
    return _store
