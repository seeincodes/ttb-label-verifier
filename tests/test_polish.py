"""Tests for Task Group 13 polish — error boundaries, loading state, a11y.

These pin observable behavior in the rendered HTML so a refactor doesn't
silently drop a Sarah-Chen-grandmother UX affordance.
"""
from __future__ import annotations

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
from tests._helpers import good_synthetic_png


def _canonical_warning_text():
    from app.verifier.warning import canonical_warning_text

    return canonical_warning_text()


def _fake_label() -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value="OLD TOM DISTILLERY", confidence="high"),
        class_type=ExtractedField(value="Kentucky Straight Bourbon Whiskey", confidence="high"),
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


class StubExtractor(LabelExtractor):
    def __init__(self, canned: LabelData):
        self.canned = canned

    async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
        return self.canned


SYNTHETIC_PNG = good_synthetic_png()


@pytest.fixture
def client_with_stub():
    from app.cache import LabelDataCache, get_cache
    from app.dependencies import get_extractor
    from app.main import app

    stub = StubExtractor(_fake_label())
    cache = LabelDataCache(maxsize=4)
    app.dependency_overrides[get_extractor] = lambda: stub
    app.dependency_overrides[get_cache] = lambda: cache
    yield TestClient(app)
    app.dependency_overrides.clear()


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


class TestHTMXLoadingIndicator:
    """The form must reference an hx-indicator so the user sees feedback
    during the multi-second model call. Sarah's '≤5 s' bar is felt — the
    spinner is what makes the wait tolerable."""

    def test_form_uses_hx_indicator(self, client_with_stub):
        body = client_with_stub.get("/").text
        assert "hx-indicator" in body

    def test_indicator_element_present(self, client_with_stub):
        """The element referenced by hx-indicator must exist in the DOM and
        carry the .htmx-indicator class (HTMX's CSS hides it by default,
        shows it during requests)."""
        body = client_with_stub.get("/").text
        assert "htmx-indicator" in body  # the class HTMX toggles
        # spinner should mention a label like "Verifying"
        assert "verifying" in body.lower() or "extracting" in body.lower()


class TestFriendlyUploadErrors:
    """File too large / empty / wrong type should render the _error_panel
    fragment with a friendly message, not a JSON 400/413 detail blob that
    HTMX would swap into the result panel."""

    def test_empty_upload_returns_friendly_fragment(self, client_with_stub):
        resp = client_with_stub.post(
            "/verify",
            data=_form_data(),
            files={"image": ("blank.png", BytesIO(b""), "image/png")},
        )
        # The route used to raise HTTPException(400) → JSON detail blob.
        # After Group 13: friendly 200 fragment.
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "empty" in body or "no image" in body
        assert "<html" not in body  # fragment, not full page
        # The JSON-detail string '{"detail":' is the old-format anti-pattern
        # — make sure we don't accidentally surface it.
        assert '{"detail":' not in body

    def test_too_large_returns_friendly_fragment(self, client_with_stub):
        # 11 MB random bytes — over the 10 MB cap.
        too_big = b"x" * (11 * 1024 * 1024)
        resp = client_with_stub.post(
            "/verify",
            data=_form_data(),
            files={"image": ("huge.png", BytesIO(too_big), "image/png")},
        )
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "too large" in body or "10 mb" in body or "exceeds" in body
        assert "<html" not in body
        assert '{"detail":' not in body


class TestTimeoutFallbackMessage:
    """When both Gemini and OpenAI fail, the error fragment must surface
    both failures and mention 'fallback' so the agent knows the system
    already tried what it could."""

    def test_both_failures_surface_in_error_message(self):
        from app.cache import LabelDataCache, get_cache
        from app.dependencies import get_extractor
        from app.extractors import FallbackExtractor
        from app.extractors.gemini import ExtractorError
        from app.main import app

        class FailingExtractor(LabelExtractor):
            def __init__(self, name):
                self.name = name

            async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
                raise ExtractorError(f"{self.name}: timed out")

        fe = FallbackExtractor(
            primary=FailingExtractor("Gemini"),
            secondary=FailingExtractor("OpenAI"),
        )
        app.dependency_overrides[get_extractor] = lambda: fe
        app.dependency_overrides[get_cache] = lambda: LabelDataCache(maxsize=4)
        try:
            client = TestClient(app)
            resp = client.post(
                "/verify",
                data=_form_data(),
                files={"image": ("label.png", BytesIO(SYNTHETIC_PNG), "image/png")},
            )
            assert resp.status_code == 200
            body = resp.text.lower()
            # Both primary and secondary should be visible in the message.
            assert "gemini" in body
            assert "openai" in body
        finally:
            app.dependency_overrides.clear()


class TestVerdictAccessibility:
    """Verdict signalling must not depend on color alone — the verdict
    *word* (PASS/WARN/FAIL/ERROR) must appear in the cell text, not just
    as a Tailwind background class."""

    def test_verdict_word_appears_in_cell_text(self, client_with_stub):
        body = client_with_stub.post(
            "/verify",
            data=_form_data(),
            files={"image": ("label.png", BytesIO(SYNTHETIC_PNG), "image/png")},
        ).text
        # 'pass' should appear as cell text in the per-field table, not
        # only as a bg-emerald-50 class on the row.
        # The result panel uses an explicit <td>...{{ fv.verdict.value }}...</td>
        # cell — confirm a literal lowercase "pass" outside a class= attr.
        # Cheap heuristic: at least one occurrence of ">pass<" or > PASS<.
        normalized = body.replace(" ", "").replace("\n", "")
        assert ">pass<" in normalized.lower() or ">PASS<" in normalized

    def test_button_has_min_height_for_hit_target(self, client_with_stub):
        """Sarah Chen's 73-year-old-mother constraint: tap targets should
        be at least 44px (iOS HIG) / 48px (Material). Tailwind `min-h-11`
        = 44px or `h-12` = 48px or `py-3` ≈ 44px work."""
        body = client_with_stub.get("/").text
        # The submit button. Look for any of the common Tailwind sizes
        # we'd accept on the Verify button.
        button_size_signals = ("min-h-11", "h-12", "py-3", "min-h-[44px]")
        assert any(sig in body for sig in button_size_signals), (
            "submit button missing a large-enough min-height (Sarah Chen constraint)"
        )
