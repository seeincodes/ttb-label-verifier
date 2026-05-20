"""Tests for the /sample/{name} pre-loaded label routes.

Per MVP5, the homepage exposes three buttons that demo the full flow
without the reviewer needing to upload anything:
  - spirits-pass    — clean spirits label, overall PASS
  - abv-fail        — ABV mismatch, overall FAIL
  - warning-fail    — malformed warning, overall FAIL with 27 CFR 16.22

The sample route bypasses the extractor entirely (uses pre-canned
LabelData JSON in sample_data/) so the demo runs offline and never
burns API quota.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


class TestSampleRoute:
    def test_spirits_pass_returns_200_and_pass_banner(self):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/sample/spirits-pass")
        assert resp.status_code == 200
        body = resp.text
        assert "PASS" in body or "Pass" in body
        # Should embed the rendered form + result on one page (this isn't
        # an HTMX fragment — full page render).
        assert "<html" in body.lower()

    def test_abv_fail_returns_fail_banner_and_cfr_5_65(self):
        from app.main import app

        client = TestClient(app)
        body = client.get("/sample/abv-fail").text
        assert "FAIL" in body or "Fail" in body
        # Spirits ABV mismatch must cite 5.65.
        assert "5.65" in body

    def test_warning_fail_returns_fail_banner_and_cfr_16_22(self):
        from app.main import app

        client = TestClient(app)
        body = client.get("/sample/warning-fail").text
        assert "FAIL" in body or "Fail" in body
        # Malformed warning must cite 16.22 (formatting).
        assert "16.22" in body

    def test_unknown_sample_returns_404(self):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/sample/not-a-real-sample")
        assert resp.status_code == 404

    def test_sample_page_includes_per_field_table(self):
        from app.main import app

        client = TestClient(app)
        body = client.get("/sample/spirits-pass").text.lower()
        assert "<table" in body
        # The 7 checklist fields should be represented
        for label in ("brand_name", "alcohol_content", "net_contents", "bottler"):
            assert label in body

    def test_sample_renders_image_thumbnail(self):
        """The sample result must show the (placeholder) image so the agent
        sees what they're looking at, even on a synthetic sample."""
        from app.main import app

        client = TestClient(app)
        body = client.get("/sample/spirits-pass").text
        assert "<img" in body.lower()
