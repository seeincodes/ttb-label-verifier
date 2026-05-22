"""STR1 integration tests — image-quality pre-check on /verify and /extract.

The check fires before the extractor so a too-dark photo never burns
1.5–7s of Gemini latency just to come back as ERROR via the confidence
gate. Failure → friendly _error_panel for /verify (HTML fragment), JSON
error body for /extract.
"""
from __future__ import annotations

import base64
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

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


def _good_label() -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value="OK", confidence="high"),
        class_type=ExtractedField(value="Bourbon", confidence="high"),
        alcohol_content_pct=ExtractedField(value=45.0, confidence="high"),
        alcohol_content_text=ExtractedField(value="45% ALC./VOL.", confidence="high"),
        net_contents=ExtractedField(value="750 mL", confidence="high"),
        bottler_name=ExtractedField(value="OK LLC", confidence="high"),
        bottler_address=ExtractedField(value="1 Main", confidence="high"),
        country_of_origin=ExtractedField(value=None, confidence="high"),
        government_warning_text=ExtractedField(value=_canonical_warning_text(), confidence="high"),
        government_warning_formatting=WarningFormatting(
            caps_correct=True, bold_correct=True, continuous=True, confidence="high"
        ),
    )


class CountingExtractor(LabelExtractor):
    """Counts calls so we can prove the extractor is never invoked on a
    pre-check failure — saves API quota AND validates the route ordering."""

    def __init__(self, canned: LabelData) -> None:
        self.canned = canned
        self.calls = 0

    async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
        self.calls += 1
        return self.canned


def _png(luma: int, size: tuple[int, int] = (400, 400)) -> bytes:
    img = Image.new("RGB", size, (luma, luma, luma))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _checkerboard_png() -> bytes:
    img = Image.new("RGB", (400, 400), (220, 220, 220))
    pixels = img.load()
    for y in range(400):
        for x in range(400):
            v = 30 if ((x // 50) + (y // 50)) % 2 == 0 else 220
            pixels[x, y] = (v, v, v)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client_with_stub():
    from app.cache import LabelDataCache, get_cache
    from app.dependencies import get_extractor
    from app.main import app

    stub = CountingExtractor(_good_label())
    cache = LabelDataCache(maxsize=8)
    app.dependency_overrides[get_extractor] = lambda: stub
    app.dependency_overrides[get_cache] = lambda: cache
    yield TestClient(app), stub
    app.dependency_overrides.clear()


_FORM_DATA = {
    "beverage_type": "distilled_spirits",
    "brand_name": "OK",
    "class_type": "Bourbon",
    "alcohol_content_pct": "45.0",
    "net_contents": "750 mL",
    "bottler_name": "OK LLC",
    "bottler_address": "1 Main",
    "is_import": "false",
}


class TestVerifyImageQualityGate:
    def test_too_dark_image_short_circuits_extractor(self, client_with_stub):
        """A pitch-black image must never reach the model."""
        client, stub = client_with_stub
        resp = client.post(
            "/verify",
            data=_FORM_DATA,
            files={"image": ("dark.png", BytesIO(_png(5)), "image/png")},
        )
        assert resp.status_code == 200
        # Friendly error fragment, not a 5xx
        body = resp.text.lower()
        assert "dark" in body or "light" in body
        assert stub.calls == 0, "extractor must NOT be called on quality failure"

    def test_too_bright_image_short_circuits_extractor(self, client_with_stub):
        client, stub = client_with_stub
        resp = client.post(
            "/verify",
            data=_FORM_DATA,
            files={"image": ("bright.png", BytesIO(_png(250)), "image/png")},
        )
        assert resp.status_code == 200
        assert stub.calls == 0

    def test_good_image_proceeds_to_extractor(self, client_with_stub):
        client, stub = client_with_stub
        resp = client.post(
            "/verify",
            data=_FORM_DATA,
            files={"image": ("ok.png", BytesIO(_checkerboard_png()), "image/png")},
        )
        assert resp.status_code == 200
        assert stub.calls == 1


class TestExtractImageQualityGate:
    """/extract returns JSON, not HTML — error shape is different."""

    def test_too_dark_image_returns_json_error_not_extracting(self, client_with_stub):
        client, stub = client_with_stub
        resp = client.post(
            "/extract",
            files={"image": ("dark.png", BytesIO(_png(5)), "image/png")},
        )
        assert resp.status_code in (400, 422)
        body = resp.json()
        assert "error" in body or "detail" in body
        # Stub never called
        assert stub.calls == 0

    def test_good_image_returns_prefill(self, client_with_stub):
        client, stub = client_with_stub
        resp = client.post(
            "/extract",
            files={"image": ("ok.png", BytesIO(_checkerboard_png()), "image/png")},
        )
        assert resp.status_code == 200
        assert stub.calls == 1
