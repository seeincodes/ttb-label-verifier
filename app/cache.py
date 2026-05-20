"""In-memory LRU cache for extracted `LabelData`, keyed by image SHA-256.

The cache stores the *extraction* result, not the verification result —
re-running the verifier on a cache hit is sub-millisecond (verifier is
pure-Python) and lets the caller re-evaluate against fresh expected
application data without re-paying for the model call.

Cache hit path returns in well under 100 ms (MVP10) — verified by the
unit test suite. The cache is in-memory only per the locked stack
decision in CLAUDE.md (production path documented in MEMO §10).
"""
from __future__ import annotations

import hashlib
import threading
from functools import lru_cache
from typing import Optional

from cachetools import LRUCache

from app.config import get_settings
from app.models import LabelData


class LabelDataCache:
    """Thread-safe LRU cache. Wraps `cachetools.LRUCache` with a `Lock`
    so the batch flow's bounded concurrency (asyncio.Semaphore, plus the
    GIL but with awaits in between) can't race a `put` against a `get`."""

    def __init__(self, maxsize: int) -> None:
        self._cache: LRUCache = LRUCache(maxsize=maxsize)
        self._lock = threading.Lock()

    @property
    def maxsize(self) -> int:
        return self._cache.maxsize

    @staticmethod
    def key_for(image_bytes: bytes) -> str:
        """SHA-256 hex digest of the raw image bytes.

        The hash is deterministic across processes / runs, so a request
        for the same bytes always lands the same key — important when the
        cache is later promoted from in-memory to Redis (the MEMO §10
        production path). Hex digest (64 chars) is plenty of bits for
        prototype-scale collision resistance and is easier to log than
        binary.
        """
        return hashlib.sha256(image_bytes).hexdigest()

    def get(self, key: str) -> Optional[LabelData]:
        with self._lock:
            return self._cache.get(key)

    def put(self, key: str, value: LabelData) -> None:
        with self._lock:
            self._cache[key] = value

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


@lru_cache(maxsize=1)
def get_cache() -> LabelDataCache:
    """Process-singleton cache, sized from CACHE_MAXSIZE."""
    settings = get_settings()
    return LabelDataCache(maxsize=settings.cache_maxsize)
