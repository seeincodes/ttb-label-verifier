"""Tests for POST /verify/json — the JSON-upload alternative to /verify.

Same downstream flow (extract → verify → render result fragment); only
the input shape for `application` changes. JSON validation errors must
surface in the UI as a graceful _error_panel, not a 500 / 422 raw.
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


def _fake_label() -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value="OLD TOM DISTILLERY", confidence="high"),
        class_type=ExtractedField(
            value="Kentucky Straight Bourbon Whiskey", confidence="high"
        ),
        alcohol_content_pct=ExtractedField(value=45.0, confidence="high"),
        alcohol_content_text=ExtractedField(
            value="45% ALC./VOL.", confidence="high"
        ),
        net_contents=ExtractedField(value="750 mL", confidence="high"),
        bottler_name=ExtractedField(value="Old Tom Distillery LLC", confidence="high"),
        bottler_address=ExtractedField(
            value="123 Distillery Rd, Frankfort, KY", confidence="high"
        ),
        country_of_origin=ExtractedField(value=None, confidence="high"),
        government_warning_text=ExtractedField(
            value=_canonical_warning_text(), confidence="high"
        ),
        government_warning_formatting=WarningFormatting(
            caps_correct=True,
            bold_correct=True,
            continuous=True,
            confidence="high",
        ),
    )


class StubExtractor(LabelExtractor):
    def __init__(self, canned: LabelData) -> None:
        self.canned = canned

    async def extract(
        self,
        image_bytes: bytes,
        beverage_type: BeverageType,
        mime_type: str = "image/jpeg",
    ) -> LabelData:
        return self.canned


SYNTHETIC_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgAQMAAABJtOi3AAAABlBMVEX///8AAA"
    "BVwtN+AAAAEElEQVR4nGNgGAWjYBSMAggAAQEAAAGYG3SXAAAAAElFTkSuQmCC"
)


VALID_APPLICATION_JSON = json.dumps(
    {
        "beverage_type": "distilled_spirits",
        "brand_name": "Old Tom Distillery",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content_pct": 45.0,
        "net_contents": "750 mL",
        "bottler_name": "Old Tom Distillery LLC",
        "bottler_address": "123 Distillery Rd, Frankfort, KY",
        "is_import": False,
    }
)


@pytest.fixture
def client_and_stub():
    from app.cache import LabelDataCache, get_cache
    from app.dependencies import get_extractor
    from app.main import app

    stub = StubExtractor(_fake_label())
    fresh_cache = LabelDataCache(maxsize=4)
    app.dependency_overrides[get_extractor] = lambda: stub
    app.dependency_overrides[get_cache] = lambda: fresh_cache
    yield TestClient(app), stub
    app.dependency_overrides.clear()


def _files():
    return {"image": ("label.png", BytesIO(SYNTHETIC_PNG), "image/png")}


class TestVerifyJsonRoute:
    def test_accepts_json_application_string_and_returns_result(self, client_and_stub):
        client, _ = client_and_stub
        resp = client.post(
            "/verify/json",
            data={"application_json": VALID_APPLICATION_JSON},
            files=_files(),
        )
        assert resp.status_code == 200
        body = resp.text
        assert "PASS" in body or "Pass" in body
        # fragment, not full page
        assert "<html" not in body.lower()

    def test_invalid_json_returns_error_panel_not_500(self, client_and_stub):
        client, _ = client_and_stub
        resp = client.post(
            "/verify/json",
            data={"application_json": "{ not valid json"},
            files=_files(),
        )
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "error" in body
        assert "json" in body
        assert "<html" not in body  # fragment

    def test_pydantic_validation_error_surfaces_in_ui(self, client_and_stub):
        """Missing required field (e.g. brand_name) → ApplicationData
        ValidationError → render the _error_panel with the field name,
        not a 500 / 422 JSON response."""
        client, _ = client_and_stub
        payload = json.loads(VALID_APPLICATION_JSON)
        del payload["brand_name"]
        resp = client.post(
            "/verify/json",
            data={"application_json": json.dumps(payload)},
            files=_files(),
        )
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "error" in body
        assert "brand" in body or "validation" in body

    def test_import_country_consistency_violation_surfaces(self, client_and_stub):
        """is_import=true + country_of_origin missing → ApplicationData's
        model_validator raises → user sees the message."""
        client, _ = client_and_stub
        payload = json.loads(VALID_APPLICATION_JSON)
        payload["is_import"] = True
        # country_of_origin omitted
        resp = client.post(
            "/verify/json",
            data={"application_json": json.dumps(payload)},
            files=_files(),
        )
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "country" in body or "import" in body

    def test_image_still_required(self, client_and_stub):
        """JSON path doesn't skip the image — still needs the multipart upload."""
        client, _ = client_and_stub
        # Send no image — FastAPI should reject as 422 form validation.
        resp = client.post(
            "/verify/json",
            data={"application_json": VALID_APPLICATION_JSON},
        )
        assert resp.status_code in (400, 422)
