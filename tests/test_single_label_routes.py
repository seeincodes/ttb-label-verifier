"""Tests for the single-label UI routes — GET / and POST /verify.

The route depends on a `LabelExtractor`; tests override that dependency
with a stub so the suite never hits the real Gemini SDK. Production
wiring uses `GeminiExtractor.from_settings()` (registered in app.main).
"""
from __future__ import annotations

from io import BytesIO
from typing import Optional

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


def _fake_label(
    *,
    brand="OLD TOM DISTILLERY",
    class_type="Kentucky Straight Bourbon Whiskey",
    abv_pct=45.0,
    abv_text="45% ALC./VOL. (90 PROOF)",
    net="750 mL",
    bottler="Old Tom Distillery LLC",
    address="123 Distillery Rd, Frankfort, KY",
    country=None,
    warning_text: Optional[str] = None,
    caps=True,
    bold=True,
    continuous=True,
) -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value=brand, confidence="high"),
        class_type=ExtractedField(value=class_type, confidence="high"),
        alcohol_content_pct=ExtractedField(value=abv_pct, confidence="high"),
        alcohol_content_text=ExtractedField(value=abv_text, confidence="high"),
        net_contents=ExtractedField(value=net, confidence="high"),
        bottler_name=ExtractedField(value=bottler, confidence="high"),
        bottler_address=ExtractedField(value=address, confidence="high"),
        country_of_origin=ExtractedField(value=country, confidence="high"),
        government_warning_text=ExtractedField(
            value=warning_text if warning_text is not None else _canonical_warning_text(),
            confidence="high",
        ),
        government_warning_formatting=WarningFormatting(
            caps_correct=caps,
            bold_correct=bold,
            continuous=continuous,
            confidence="high",
        ),
    )


class StubExtractor(LabelExtractor):
    """Returns a canned LabelData. Tests set `.canned` per case."""

    def __init__(self, canned: LabelData) -> None:
        self.canned = canned
        self.calls: list[tuple[int, BeverageType, str]] = []

    async def extract(
        self, image_bytes: bytes, beverage_type: BeverageType, mime_type: str = "image/jpeg"
    ) -> LabelData:
        self.calls.append((len(image_bytes), beverage_type, mime_type))
        return self.canned


@pytest.fixture
def make_client():
    """Build a TestClient with extractor + cache dependencies overridden.

    Each test gets a fresh LabelDataCache so cached extractions from a
    previous test don't leak in (the production cache is a process
    singleton; tests must isolate)."""
    from app.cache import LabelDataCache
    from app.dependencies import get_extractor
    from app.cache import get_cache
    from app.main import app

    def _make(canned: LabelData) -> tuple[TestClient, StubExtractor]:
        stub = StubExtractor(canned)
        fresh_cache = LabelDataCache(maxsize=4)
        app.dependency_overrides[get_extractor] = lambda: stub
        app.dependency_overrides[get_cache] = lambda: fresh_cache
        return TestClient(app), stub

    yield _make
    from app.main import app as _app

    _app.dependency_overrides.clear()


# Minimal one-pixel PNG (the same payload smoke_extractor uses).

SYNTHETIC_PNG = good_synthetic_png()


# ---------------------------------------------------------------------------
# GET / — single-label upload form
# ---------------------------------------------------------------------------


class TestGetIndex:
    def test_returns_200_and_html(self):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_renders_form_with_required_fields(self):
        from app.main import app

        client = TestClient(app)
        body = client.get("/").text.lower()

        # Form must support file upload + multipart.
        assert "<form" in body
        assert 'enctype="multipart/form-data"' in body or "multipart" in body
        assert 'type="file"' in body
        assert 'name="image"' in body

        # All 7 expected-data input names per MVP2.
        for name in (
            "beverage_type",
            "brand_name",
            "class_type",
            "alcohol_content_pct",
            "net_contents",
            "bottler_name",
            "bottler_address",
        ):
            assert f'name="{name}"' in body, f"form missing field {name}"

        # is_import + country_of_origin
        assert 'name="is_import"' in body
        assert 'name="country_of_origin"' in body

    def test_form_uses_htmx_post_to_verify(self):
        from app.main import app

        client = TestClient(app)
        body = client.get("/").text
        assert 'hx-post="/verify"' in body
        # HTMX swap target must be present so the result fragment can land.
        assert "hx-target=" in body

    def test_renders_three_sample_buttons(self):
        """Sarah's 'pre-loaded samples' (MVP5) — three buttons on the home
        page so the reviewer can see the full flow without uploading."""
        from app.main import app

        client = TestClient(app)
        body = client.get("/").text.lower()
        # Either three buttons / three GET links to /sample/{name}.
        for sample in ("spirits-pass", "abv-fail", "warning-fail"):
            assert sample in body, f"sample button {sample} missing"

    def test_form_wires_upload_prefill_to_extract_route(self):
        """The form's file-input change handler must call POST /extract so
        the agent's typing is reduced. Pinned at the affordance level so
        a CSS / Alpine refactor doesn't silently drop the wiring."""
        from app.main import app

        client = TestClient(app)
        body = client.get("/").text
        # Alpine component is named verifyForm() and posts to /extract.
        assert "verifyForm()" in body
        assert "/extract" in body
        # x-model bindings exist so the prefill can populate the inputs.
        for field in ("brand_name", "alcohol_content_pct", "bottler_name"):
            assert f'x-model="fields.{field}"' in body, (
                f"prefill x-model binding missing for {field}"
            )

    def test_prefill_visual_cue_present(self):
        """Pre-filled fields must look different so the agent eyes are drawn
        to whether they confirmed/edited each one before clicking Verify.
        Either an amber-family ring/border or a 'suggested' label per field.
        Accepts Tailwind opacity-modified syntax (`amber-700/40`) since the
        editorial redesign uses tonal amber against cream paper rather than
        the saturated amber-300 of the original."""
        from app.main import app

        client = TestClient(app)
        body = client.get("/").text
        # Visual cue: any amber-family class. Textual cue: "suggested" word.
        assert "amber-" in body, "no amber-family prefill styling found"
        assert "suggested" in body.lower()

    def test_prefill_runs_in_background_not_blocking(self):
        """Gemini latency on real labels is 7–9s; 'Reading the label…' as a
        modal-feeling spinner makes the agent think they have to wait. The
        UI must signal that the form is usable RIGHT NOW and prefill is
        background work — onPick must not await the fetch, and the progress
        message must describe a background activity, not a wait gate."""
        from app.main import app

        body = TestClient(app).get("/").text
        # onPick fires the extract call without awaiting it — the agent
        # can start typing while Gemini works. The current scope binds
        # the result via .then() / no-await; either form is acceptable
        # as long as onPick isn't a single `await this.extract(file)` line.
        # We can't run JS in pytest, so pin the textual affordance:
        body_lower = body.lower()
        # The progress message must NOT phrase the wait as gated — phrases
        # like "please wait" or "loading…" are anti-patterns here. The new
        # wording explicitly says the agent can keep typing.
        assert (
            "you can start typing" in body_lower
            or "while we read" in body_lower
            or "background" in body_lower
        ), "progress message must signal that prefill is background work"

    def test_elapsed_time_counter_visible_during_prefill(self):
        """7–9s of static spinner feels broken. An elapsed-time counter
        gives the agent a signal that the call is still alive AND lets
        them decide whether to wait for the prefill or push on without it."""
        from app.main import app

        body = TestClient(app).get("/").text
        # Either a literal "elapsed" word in the UI strings, or an
        # `elapsedSeconds` reactive in the Alpine component, or a
        # `setInterval` driving a per-second tick — any of these counts
        # as the affordance.
        assert (
            "elapsed" in body.lower()
            or "elapsedseconds" in body.lower()
            or "setinterval" in body.lower()
        ), "elapsed-time counter affordance missing from the prefill UI"


# ---------------------------------------------------------------------------
# POST /verify — full round-trip with stubbed extractor
# ---------------------------------------------------------------------------


def _form_data(**overrides):
    data = {
        "beverage_type": "distilled_spirits",
        "brand_name": "Old Tom Distillery",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content_pct": "45.0",
        "net_contents": "750 mL",
        "bottler_name": "Old Tom Distillery LLC",
        "bottler_address": "123 Distillery Rd, Frankfort, KY",
        "is_import": "false",
    }
    data.update({k: str(v) for k, v in overrides.items()})
    return data


def _files():
    return {"image": ("label.png", BytesIO(SYNTHETIC_PNG), "image/png")}


class TestPostVerify:
    def test_happy_path_returns_pass_fragment(self, make_client):
        client, stub = make_client(_fake_label())
        resp = client.post("/verify", data=_form_data(), files=_files())

        assert resp.status_code == 200
        body = resp.text

        # Result panel must include the verdict banner.
        assert "PASS" in body or "Pass" in body
        # And the per-field table.
        assert "<table" in body.lower()
        # Stub was actually called (the round trip really happened, not a stub-form leak).
        assert len(stub.calls) == 1

    def test_response_is_fragment_not_full_page(self, make_client):
        """HTMX swaps a fragment; the response must NOT include a full <html>
        wrapper, otherwise the swap renders garbled per ERROR_FIX_LOG."""
        client, _ = make_client(_fake_label())
        body = client.post("/verify", data=_form_data(), files=_files()).text
        assert "<html" not in body.lower()
        assert "<!doctype" not in body.lower()

    def test_response_includes_image_thumbnail(self, make_client):
        client, _ = make_client(_fake_label())
        body = client.post("/verify", data=_form_data(), files=_files()).text
        assert "<img" in body.lower()
        # Image should be inline (data: URL) since we don't persist uploads.
        assert "data:image" in body.lower()

    def test_response_includes_verdict_banner_color_class(self, make_client):
        """Verdict banner carries a verdict-tone class per §5.4. Accepts
        either the original Tailwind palette names (bg-emerald, bg-green)
        or the editorial-design verdict-* semantic tokens (verdict-pass etc.).
        What matters is the *contract*: a distinct visual token per verdict."""
        client, _ = make_client(_fake_label())
        body = client.post("/verify", data=_form_data(), files=_files()).text
        assert (
            "bg-green" in body
            or "bg-emerald" in body
            or "border-green" in body
            or "border-emerald" in body
            or "verdict-pass" in body
        ), "PASS verdict must carry a green-family or verdict-pass tone class"

    def test_response_includes_per_field_table_columns(self, make_client):
        """Per MVP4: table shows extracted | expected | verdict | reasoning |
        CFR citation. The headers should be present."""
        client, _ = make_client(_fake_label())
        body = client.post("/verify", data=_form_data(), files=_files()).text.lower()
        # at minimum we need column headers reflecting the spec
        for header in ("extracted", "expected", "verdict"):
            assert header in body, f"per-field table missing column: {header}"
        # plus reasoning + cite — present on FAIL/WARN rows but the column
        # header may be omitted on all-PASS. Check that one of them shows.
        assert "cfr" in body or "27 cfr" in body or "reason" in body

    def test_fail_label_shows_red_banner(self, make_client):
        """ABV 'ABV' literal on the label → FAIL → red-family tone class.
        Accepts the original Tailwind palette names (bg-red, bg-rose) or
        the editorial-design semantic tokens (verdict-fail, the oxblood
        --seal). The contract is "visually distinguishable as a FAIL"."""
        client, _ = make_client(_fake_label(abv_text="45% ABV"))
        body = client.post("/verify", data=_form_data(), files=_files()).text
        assert "FAIL" in body or "Fail" in body
        assert (
            "bg-red" in body
            or "bg-rose" in body
            or "border-red" in body
            or "border-rose" in body
            or "verdict-fail" in body
        ), "FAIL verdict must carry a red-family or verdict-fail tone class"

    def test_raw_extraction_panel_present(self, make_client):
        """MVP4 audit-panel signal: a collapsible 'view raw extraction'
        section with the JSON shape from §5.5."""
        client, _ = make_client(_fake_label())
        body = client.post("/verify", data=_form_data(), files=_files()).text.lower()
        # collapsible affordance — <details> + <summary> is the right semantic
        assert "<details" in body or "x-show" in body
        # The brand-name JSON value should appear somewhere in the panel.
        assert "old tom distillery" in body

    def test_extractor_receives_correct_beverage_type(self, make_client):
        client, stub = make_client(_fake_label())
        client.post(
            "/verify",
            data=_form_data(beverage_type="wine"),
            files=_files(),
        )
        assert stub.calls[0][1] is BeverageType.WINE

    def test_import_with_country_runs_country_rule(self, make_client):
        """is_import=true + country_of_origin must reach the verifier."""
        client, _ = make_client(_fake_label(country="Scotland"))
        body = client.post(
            "/verify",
            data=_form_data(
                is_import="true",
                country_of_origin="Scotland",
            ),
            files=_files(),
        ).text.lower()
        assert "country" in body or "scotland" in body

    def test_extractor_error_renders_error_panel(self, make_client):
        """If the extractor raises ExtractorError, the user must see a
        graceful failure card, not a 500 page."""
        from app.cache import LabelDataCache, get_cache
        from app.dependencies import get_extractor
        from app.extractors.gemini import ExtractorError
        from app.main import app

        class FailingExtractor(LabelExtractor):
            async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
                raise ExtractorError("vision model: 503 UNAVAILABLE")

        # Fresh cache too — otherwise a hit from an earlier test would
        # short-circuit the extractor and we'd never see the error path.
        app.dependency_overrides[get_extractor] = lambda: FailingExtractor()
        app.dependency_overrides[get_cache] = lambda: LabelDataCache(maxsize=4)
        try:
            client = TestClient(app)
            resp = client.post("/verify", data=_form_data(), files=_files())
            # Should still be a 200 with a friendly card — not a 500 traceback.
            assert resp.status_code == 200
            body = resp.text.lower()
            assert "error" in body
            assert "503" in body or "unavailable" in body or "again" in body
        finally:
            app.dependency_overrides.clear()
