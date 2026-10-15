"""A small distributed lock on the store, for CI systems without concurrency groups (Azure DevOps).

The lock is one object, `locks/<name>.json`, taken with a conditional write so exactly one holder wins.
It carries an expiry, so a run that is killed without releasing cannot block everyone forever.
Releasing rewrites the record (no delete permission needed).
"""
from __future__ import annotations

import datetime as dt
import re
import time
from typing import Callable

from . import manifest as mf
from .store import Store

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class LockBusy(Exception):
    pass


def _key(name: str) -> str:
    if not NAME_RE.match(name):
        raise ValueError("invalid lock name %r" % name)
    return "locks/%s.json" % name


def acquire(store: Store, name: str, owner: str, ttl_seconds: int, wait_seconds: int = 0,
            now: Callable[[], dt.datetime] = mf.utcnow, sleep: Callable[[float], None] = time.sleep,
            poll_seconds: int = 15) -> None:
    key = _key(name)
    deadline = now() + dt.timedelta(seconds=wait_seconds)
    while True:
        record = {"owner": owner, "released": False, "acquired_at": mf.iso(now()),
                  "expires_at": mf.iso(now() + dt.timedelta(seconds=ttl_seconds))}
        held, etag = store.get_with_etag(key)
        if held is None:
            if store.put_if_absent(key, record):
                return
        elif held["released"] or mf.parse_iso(held["expires_at"]) <= now() or held["owner"] == owner:
            # free, expired (holder presumably died) or ours (a retried step): take it, but only if
            # nobody changed it since we looked.
            if store.put_if_match(key, record, etag):
                return
        if now() >= deadline:
            raise LockBusy("lock %r is held by %s until %s" % (name, held and held["owner"], held and held["expires_at"]))
        sleep(poll_seconds)


def release(store: Store, name: str, owner: str, now: Callable[[], dt.datetime] = mf.utcnow) -> bool:
    """Release if we still hold it. Never fails a pipeline: an expired and re-taken lock is not ours to release."""
    key = _key(name)
    held, etag = store.get_with_etag(key)
    if held is None or held["owner"] != owner or held["released"]:
        return False
    held.update(released=True, expires_at=mf.iso(now()))
    return store.put_if_match(key, held, etag)
