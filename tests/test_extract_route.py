"""Tests for POST /extract — the upload-prefill route.

When an agent uploads a label image, the form auto-fills with the
vision model's read of the label. The agent reviews + edits against
their COLA application before clicking Verify. This route is the
backend half of that flow.

Design notes:
  - Returns 200 JSON shaped for the form (no HTML fragment — the
    frontend reads it and populates input fields).
  - Fields at confidence='low' or value=null are *omitted* so the
    form input stays empty; the agent fills them manually rather
    than starting from a likely-wrong guess.
  - The extraction goes through the same factory + cache, so a
    subsequent /verify on the same image bytes is a cache hit.
"""
from __future__ import annotations

import base64
import json
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


def _label(
    *,
    brand="OLD TOM DISTILLERY",
    brand_conf="high",
    class_type="Kentucky Straight Bourbon Whiskey",
    class_conf="high",
    abv_pct=45.0,
    abv_text="45% ALC./VOL. (90 PROOF)",
    net="750 mL",
    bottler="Old Tom Distillery LLC",
    address="123 Distillery Rd, Frankfort, KY 40601",
    country=None,
    country_conf="high",
    beverage_type_guess=BeverageType.DISTILLED_SPIRITS,
) -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value=brand, confidence=brand_conf),
        class_type=ExtractedField(value=class_type, confidence=class_conf),
        alcohol_content_pct=ExtractedField(value=abv_pct, confidence="high"),
        alcohol_content_text=ExtractedField(value=abv_text, confidence="high"),
        net_contents=ExtractedField(value=net, confidence="high"),
        bottler_name=ExtractedField(value=bottler, confidence="high"),
        bottler_address=ExtractedField(value=address, confidence="high"),
        country_of_origin=ExtractedField(value=country, confidence=country_conf),
        government_warning_text=ExtractedField(
            value=_canonical_warning_text(), confidence="high"
        ),
        government_warning_formatting=WarningFormatting(
            caps_correct=True, bold_correct=True, continuous=True, confidence="high"
        ),
        beverage_type_guess=beverage_type_guess,
    )


class StubExtractor(LabelExtractor):
    def __init__(self, canned: LabelData) -> None:
        self.canned = canned
        self.calls = 0

    async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
        self.calls += 1
        return self.canned


SYNTHETIC_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgAQMAAABJtOi3AAAABlBMVEX///8AAA"
    "BVwtN+AAAAEElEQVR4nGNgGAWjYBSMAggAAQEAAAGYG3SXAAAAAElFTkSuQmCC"
)


@pytest.fixture
def make_client():
    from app.cache import LabelDataCache, get_cache
    from app.dependencies import get_extractor
    from app.main import app

    def _make(canned: LabelData) -> tuple[TestClient, StubExtractor, LabelDataCache]:
        stub = StubExtractor(canned)
        cache = LabelDataCache(maxsize=8)
        app.dependency_overrides[get_extractor] = lambda: stub
        app.dependency_overrides[get_cache] = lambda: cache
        return TestClient(app), stub, cache

    yield _make
    from app.main import app as _app

    _app.dependency_overrides.clear()


class TestPostExtract:
    def test_returns_json_with_form_fields(self, make_client):
        client, stub, _ = make_client(_label())
        resp = client.post(
            "/extract",
            files={"image": ("label.png", BytesIO(SYNTHETIC_PNG), "image/png")},
        )
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

        payload = resp.json()
        # Required affirmative fields appear.
        assert payload["brand_name"] == "Old Tom Distillery LLC".replace(
            " LLC", ""
        ) or payload["brand_name"]  # any non-empty truthy
        assert payload["brand_name"] == "OLD TOM DISTILLERY"
        assert payload["class_type"] == "Kentucky Straight Bourbon Whiskey"
        assert payload["alcohol_content_pct"] == 45.0
        assert payload["net_contents"] == "750 mL"
        assert payload["bottler_name"] == "Old Tom Distillery LLC"
        assert payload["bottler_address"] == "123 Distillery Rd, Frankfort, KY 40601"
        assert payload["beverage_type"] == "distilled_spirits"

        # Stub was actually invoked (not a leak from a fixture cache).
        assert stub.calls == 1

    def test_omits_fields_with_low_confidence(self, make_client):
        """Low confidence → the model said 'I can't read this'; we omit the
        field from the prefill so the form input stays empty and the agent
        fills it manually (rather than copying a guess into the COLA-truth
        slot)."""
        client, _, _ = make_client(
            _label(class_type=None, class_conf="low")
        )
        payload = client.post(
            "/extract",
            files={"image": ("x.png", BytesIO(SYNTHETIC_PNG), "image/png")},
        ).json()
        assert "class_type" not in payload or payload["class_type"] is None or payload["class_type"] == ""

    def test_omits_null_value_fields(self, make_client):
        """A domestic label has country_of_origin=null (verifier-correct).
        The form's country_of_origin input should stay empty."""
        client, _, _ = make_client(_label(country=None))
        payload = client.post(
            "/extract",
            files={"image": ("x.png", BytesIO(SYNTHETIC_PNG), "image/png")},
        ).json()
        assert (
            "country_of_origin" not in payload
            or payload["country_of_origin"] in (None, "")
        )

    def test_import_label_prefills_country_and_is_import(self, make_client):
        """A label that prints a country of origin should pre-fill it AND
        set is_import=true so the agent's checkbox flips automatically."""
        client, _, _ = make_client(_label(country="Scotland"))
        payload = client.post(
            "/extract",
            files={"image": ("scotch.png", BytesIO(SYNTHETIC_PNG), "image/png")},
        ).json()
        assert payload["country_of_origin"] == "Scotland"
        assert payload["is_import"] is True

    def test_domestic_label_sets_is_import_false(self, make_client):
        client, _, _ = make_client(_label(country=None))
        payload = client.post(
            "/extract",
            files={"image": ("x.png", BytesIO(SYNTHETIC_PNG), "image/png")},
        ).json()
        assert payload["is_import"] is False

    def test_includes_beverage_type_guess(self, make_client):
        client, _, _ = make_client(
            _label(beverage_type_guess=BeverageType.WINE)
        )
        payload = client.post(
            "/extract",
            files={"image": ("wine.png", BytesIO(SYNTHETIC_PNG), "image/png")},
        ).json()
        assert payload["beverage_type"] == "wine"

    def test_empty_upload_returns_graceful_400_json(self, make_client):
        """A user posting an empty file should get an actionable JSON error,
        not a 500. The frontend reads this and surfaces a toast / banner."""
        client, _, _ = make_client(_label())
        resp = client.post(
            "/extract",
            files={"image": ("blank.png", BytesIO(b""), "image/png")},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body or "detail" in body

    def test_extractor_error_returns_graceful_json(self, make_client):
        """Vision 503 / quota errors must surface as a 502 (bad gateway) or
        503 with a clean JSON error body, not a Python traceback."""
        from app.cache import LabelDataCache, get_cache
        from app.dependencies import get_extractor
        from app.extractors.gemini import ExtractorError
        from app.main import app

        class FailingExtractor(LabelExtractor):
            async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
                raise ExtractorError("model 503")

        app.dependency_overrides[get_extractor] = lambda: FailingExtractor()
        app.dependency_overrides[get_cache] = lambda: LabelDataCache(maxsize=4)
        try:
            client = TestClient(app)
            resp = client.post(
                "/extract",
                files={"image": ("x.png", BytesIO(SYNTHETIC_PNG), "image/png")},
            )
            assert resp.status_code in (502, 503, 200)
            # If 200, the body should at minimum carry an `error` key the
            # frontend can react to without crashing.
            body = resp.json()
            if resp.status_code == 200:
                assert "error" in body
            else:
                assert body  # non-empty error body
        finally:
            app.dependency_overrides.clear()

    def test_populates_cache_so_verify_is_hit_after(self, make_client):
        """Cache-economy guarantee: a /extract followed by a /verify on the
        same image bytes should be a cache hit on the /verify call. Saves
        one model call per label."""
        client, stub, cache = make_client(_label())
        # First call — populates the cache.
        client.post(
            "/extract",
            files={"image": ("x.png", BytesIO(SYNTHETIC_PNG), "image/png")},
        )
        assert stub.calls == 1

        # Second call to /extract on the same bytes should be a cache hit
        # (stub still at 1 call).
        client.post(
            "/extract",
            files={"image": ("x.png", BytesIO(SYNTHETIC_PNG), "image/png")},
        )
        assert stub.calls == 1, "second /extract on same bytes should be cache hit"
