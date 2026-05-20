"""Tests for app.cache — SHA-256-keyed in-memory LabelData LRU."""
from __future__ import annotations

import hashlib

import pytest

from app.models import ExtractedField, LabelData, WarningFormatting


def _canonical_warning_text():
    from app.verifier.warning import canonical_warning_text

    return canonical_warning_text()


def _fake_label(brand="Acme") -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value=brand, confidence="high"),
        class_type=ExtractedField(value="Bourbon", confidence="high"),
        alcohol_content_pct=ExtractedField(value=45.0, confidence="high"),
        alcohol_content_text=ExtractedField(value="45% ALC./VOL.", confidence="high"),
        net_contents=ExtractedField(value="750 mL", confidence="high"),
        bottler_name=ExtractedField(value="Acme", confidence="high"),
        bottler_address=ExtractedField(value="1 Main", confidence="high"),
        country_of_origin=ExtractedField(value=None, confidence="high"),
        government_warning_text=ExtractedField(
            value=_canonical_warning_text(), confidence="high"
        ),
        government_warning_formatting=WarningFormatting(
            caps_correct=True, bold_correct=True, continuous=True, confidence="high"
        ),
    )


class TestKeyFor:
    def test_returns_sha256_hex_digest(self):
        from app.cache import LabelDataCache

        cache = LabelDataCache(maxsize=4)
        key = cache.key_for(b"hello world")
        assert key == hashlib.sha256(b"hello world").hexdigest()
        assert len(key) == 64

    def test_same_bytes_same_key(self):
        from app.cache import LabelDataCache

        cache = LabelDataCache(maxsize=4)
        assert cache.key_for(b"abc") == cache.key_for(b"abc")

    def test_different_bytes_different_key(self):
        from app.cache import LabelDataCache

        cache = LabelDataCache(maxsize=4)
        assert cache.key_for(b"abc") != cache.key_for(b"abd")


class TestGetPut:
    def test_miss_returns_none(self):
        from app.cache import LabelDataCache

        cache = LabelDataCache(maxsize=4)
        assert cache.get(cache.key_for(b"x")) is None

    def test_round_trip(self):
        from app.cache import LabelDataCache

        cache = LabelDataCache(maxsize=4)
        key = cache.key_for(b"x")
        original = _fake_label("Old Tom")
        cache.put(key, original)
        cached = cache.get(key)
        assert cached is not None
        assert cached.brand_name.value == "Old Tom"
        # Reference identity: cache stores the LabelData as-is.
        assert cached is original or cached == original

    def test_lru_eviction_at_maxsize(self):
        from app.cache import LabelDataCache

        cache = LabelDataCache(maxsize=2)
        k1 = cache.key_for(b"a")
        k2 = cache.key_for(b"b")
        k3 = cache.key_for(b"c")
        cache.put(k1, _fake_label("A"))
        cache.put(k2, _fake_label("B"))
        cache.put(k3, _fake_label("C"))
        # k1 should have been evicted (it's the least recently used).
        assert cache.get(k1) is None
        assert cache.get(k2) is not None
        assert cache.get(k3) is not None

    def test_get_promotes_recency(self):
        from app.cache import LabelDataCache

        cache = LabelDataCache(maxsize=2)
        k1, k2, k3 = cache.key_for(b"a"), cache.key_for(b"b"), cache.key_for(b"c")
        cache.put(k1, _fake_label("A"))
        cache.put(k2, _fake_label("B"))
        # touch k1 so it's most recent
        cache.get(k1)
        cache.put(k3, _fake_label("C"))
        # now k2 should have been evicted, not k1
        assert cache.get(k1) is not None
        assert cache.get(k2) is None


class TestHitLatency:
    """MVP10: cache-hit path returns in < 100 ms. The verifier itself is
    sub-100 ms (verified via the per-rule tests). The cache lookup is
    a dict access, so well under the budget — pin a generous bound."""

    def test_hit_lookup_fast(self):
        import time

        from app.cache import LabelDataCache

        cache = LabelDataCache(maxsize=128)
        key = cache.key_for(b"x" * 10_000)
        cache.put(key, _fake_label())
        started = time.perf_counter()
        for _ in range(1000):
            cache.get(key)
        elapsed_ms = (time.perf_counter() - started) * 1000
        # 1000 lookups in under 100 ms = ≤ 0.1 ms each → comfortably under the
        # 100 ms MVP10 budget on the single-hit path.
        assert elapsed_ms < 100, f"1000 cache hits took {elapsed_ms:.2f}ms"


class TestSingleton:
    def test_get_cache_returns_same_instance(self):
        from app.cache import get_cache

        assert get_cache() is get_cache()

    def test_get_cache_respects_settings_maxsize(self):
        """The singleton honours CACHE_MAXSIZE from Settings."""
        from app.cache import get_cache
        from app.config import get_settings

        cache = get_cache()
        assert cache.maxsize == get_settings().cache_maxsize
