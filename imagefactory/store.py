"""Platform-neutral state: manifests and channel pointers live here, not in any cloud's API.

Platforms (AWS SSM, Azure, ...) are projections of this state, so rollback and audit work
the same everywhere.

Besides plain get/put the store offers conditional writes (create-if-absent, replace-if-unchanged)
so concurrent writers can never silently overwrite each other. S3 provides them natively
(If-None-Match / If-Match on PutObject); the local store emulates them for tests.
"""
from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _dump(obj: Dict[str, Any]) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()


class Store(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def put(self, key: str, obj: Dict[str, Any]) -> None: ...

    @abstractmethod
    def list(self, prefix: str) -> List[str]:
        """Keys under prefix (recursive), sorted."""

    @abstractmethod
    def get_with_etag(self, key: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """The object and an opaque version tag for use with put_if_match."""

    @abstractmethod
    def put_if_absent(self, key: str, obj: Dict[str, Any]) -> bool:
        """Create the object only if it does not exist. False if someone else created it first."""

    @abstractmethod
    def put_if_match(self, key: str, obj: Dict[str, Any], etag: str) -> bool:
        """Replace the object only if it is still the version `etag` came from."""


class LocalStore(Store):
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def get(self, key):
        return self.get_with_etag(key)[0]

    def get_with_etag(self, key):
        p = self._path(key)
        if not p.exists():
            return None, None
        raw = p.read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()

    def put(self, key, obj):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_dump(obj))

    def put_if_absent(self, key, obj):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "xb") as f:      # O_EXCL: exactly one creator wins
                f.write(_dump(obj))
            return True
        except FileExistsError:
            return False

    def put_if_match(self, key, obj, etag):
        _, current = self.get_with_etag(key)
        if current != etag:
            return False
        tmp = self._path(key).with_suffix(".tmp%d" % os.getpid())
        tmp.write_bytes(_dump(obj))
        os.replace(tmp, self._path(key))
        return True

    def list(self, prefix):
        if not self.root.exists():
            return []
        keys = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix) and ".tmp" not in k.rsplit("/", 1)[-1])


class S3Store(Store):
    def __init__(self, bucket: str, prefix: str = "", region: str = None, client=None):
        if client is None:
            import boto3
            client = boto3.client("s3", region_name=region)
        self.s3, self.bucket = client, bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""

    def get(self, key):
        return self.get_with_etag(key)[0]

    def get_with_etag(self, key):
        try:
            r = self.s3.get_object(Bucket=self.bucket, Key=self.prefix + key)
        except self.s3.exceptions.NoSuchKey:
            return None, None
        return json.loads(r["Body"].read()), r.get("ETag")

    def _put(self, key, obj, **cond):
        self.s3.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=_dump(obj),
                           ContentType="application/json", ServerSideEncryption="aws:kms", **cond)

    def put(self, key, obj):
        self._put(key, obj)

    def _conditional(self, key, obj, **cond) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._put(key, obj, **cond)
            return True
        except ClientError as e:
            # 412 PreconditionFailed: lost the race. 409 ConditionalRequestConflict: a concurrent
            # conditional write is in flight; either way this writer did not win.
            if e.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict"):
                return False
            raise

    def put_if_absent(self, key, obj):
        return self._conditional(key, obj, IfNoneMatch="*")

    def put_if_match(self, key, obj, etag):
        return self._conditional(key, obj, IfMatch=etag)

    def put_file(self, key: str, path) -> None:
        with open(path, "rb") as f:
            self.s3.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=f.read(), ServerSideEncryption="aws:kms")

    def list(self, prefix):
        keys, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self.prefix + prefix}
            if token:
                kw["ContinuationToken"] = token
            r = self.s3.list_objects_v2(**kw)
            keys += [o["Key"][len(self.prefix):] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return sorted(keys)
            token = r["NextContinuationToken"]


def store_from_config(cfg) -> Store:
    s = cfg.raw["store"]
    if s["type"] == "local":
        return LocalStore(s["local_path"])
    return S3Store(s["bucket"], s.get("prefix", ""), s.get("region"))
