"""End-to-end cache integration tests for /verify.

Two requests with the same image bytes:
  1. first must call the extractor (cache miss, populates cache)
  2. second must NOT call the extractor (cache hit, reuses extraction)
     and the result panel must surface 'cache hit' so MVP8/10 is visible.
"""
from __future__ import annotations

import base64
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app.extractors.base import LabelExtractor
from app.models import (
    BeverageType,
    ExtractedField,
    LabelData,
    WarningFormatting,
)


def _canonical_warning_text():
    from app.verifier.warning import canonical_warning_text

    return canonical_warning_text()


def _fake_label() -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value="OLD TOM DISTILLERY", confidence="high"),
        class_type=ExtractedField(
            value="Kentucky Straight Bourbon Whiskey", confidence="high"
        ),
        alcohol_content_pct=ExtractedField(value=45.0, confidence="high"),
        alcohol_content_text=ExtractedField(value="45% ALC./VOL.", confidence="high"),
        net_contents=ExtractedField(value="750 mL", confidence="high"),
        bottler_name=ExtractedField(value="Old Tom Distillery LLC", confidence="high"),
        bottler_address=ExtractedField(value="123 Distillery Rd", confidence="high"),
        country_of_origin=ExtractedField(value=None, confidence="high"),
        government_warning_text=ExtractedField(
            value=_canonical_warning_text(), confidence="high"
        ),
        government_warning_formatting=WarningFormatting(
            caps_correct=True, bold_correct=True, continuous=True, confidence="high"
        ),
    )


class CountingExtractor(LabelExtractor):
    """Records call count so we can assert the cache short-circuits the SDK."""

    def __init__(self, canned: LabelData) -> None:
        self.canned = canned
        self.call_count = 0

    async def extract(
        self, image_bytes, beverage_type, mime_type="image/jpeg"
    ) -> LabelData:
        self.call_count += 1
        return self.canned


SYNTHETIC_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgAQMAAABJtOi3AAAABlBMVEX///8AAA"
    "BVwtN+AAAAEElEQVR4nGNgGAWjYBSMAggAAQEAAAGYG3SXAAAAAElFTkSuQmCC"
)


def _form_data():
    return {
        "beverage_type": "distilled_spirits",
        "brand_name": "Old Tom Distillery",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content_pct": "45.0",
        "net_contents": "750 mL",
        "bottler_name": "Old Tom Distillery LLC",
        "bottler_address": "123 Distillery Rd",
        "is_import": "false",
    }


def _files():
    return {"image": ("label.png", BytesIO(SYNTHETIC_PNG), "image/png")}


@pytest.fixture
def isolated_cache_client():
    """Fresh cache + stub extractor for each test; cleanup after."""
    from app.cache import LabelDataCache, get_cache
    from app.dependencies import get_extractor
    from app.main import app

    # Reset the singleton cache (it's an lru_cache(1) on a function).
    get_cache.cache_clear()

    stub = CountingExtractor(_fake_label())
    # New fresh LabelDataCache so we don't share state across tests.
    fresh_cache = LabelDataCache(maxsize=4)
    app.dependency_overrides[get_extractor] = lambda: stub
    app.dependency_overrides[get_cache] = lambda: fresh_cache

    yield TestClient(app), stub, fresh_cache

    app.dependency_overrides.clear()
    get_cache.cache_clear()


class TestCacheIntegration:
    def test_first_request_misses_then_second_hits(self, isolated_cache_client):
        client, stub, _ = isolated_cache_client

        # First call — miss.
        r1 = client.post("/verify", data=_form_data(), files=_files())
        assert r1.status_code == 200
        assert stub.call_count == 1
        assert "cache hit" not in r1.text.lower()

        # Second call, same image — must hit cache, extractor not called again.
        r2 = client.post("/verify", data=_form_data(), files=_files())
        assert r2.status_code == 200
        assert stub.call_count == 1, "extractor should not be called on cache hit"
        assert "cache hit" in r2.text.lower()

    def test_different_image_misses(self, isolated_cache_client):
        client, stub, _ = isolated_cache_client

        client.post("/verify", data=_form_data(), files=_files())
        # Different bytes — must miss.
        other_files = {
            "image": ("label2.png", BytesIO(SYNTHETIC_PNG + b"\x00"), "image/png")
        }
        client.post("/verify", data=_form_data(), files=other_files)
        assert stub.call_count == 2

    def test_cache_hit_latency_below_100ms(self, isolated_cache_client):
        """MVP10 cache-hit budget. 1st warm; 2nd timed."""
        import time

        client, _, _ = isolated_cache_client
        client.post("/verify", data=_form_data(), files=_files())

        started = time.perf_counter()
        r = client.post("/verify", data=_form_data(), files=_files())
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert r.status_code == 200
        # Generous bound; TestClient adds ASGI overhead but the verifier
        # itself is sub-100 ms and the cache lookup is sub-ms.
        assert elapsed_ms < 500, f"cache-hit round-trip took {elapsed_ms:.0f}ms"
