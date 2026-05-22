"""Tests for app.image_quality — classical-CV pre-check before extraction.

The MVP9 confidence gate catches unreadable labels *after* the Gemini
call; this check catches them *before*, saving the latency and cost of a
doomed extraction. The heuristics are deliberately permissive — a stout
label that's mostly black is legitimately dark and must still pass.
Only obviously-unreadable photos (all-dark, all-white, low-contrast)
should fail.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image


def _png(color: tuple[int, int, int] | int, size: tuple[int, int] = (400, 400)) -> bytes:
    """Build a solid-color PNG in memory."""
    img = Image.new("RGB", size, color if isinstance(color, tuple) else (color, color, color))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _checkerboard_png(
    light: int = 220, dark: int = 30, size: tuple[int, int] = (400, 400), cells: int = 8
) -> bytes:
    """High-contrast checkerboard — exercises the 'has contrast' branch."""
    img = Image.new("RGB", size, (light, light, light))
    pixels = img.load()
    w, h = size
    cw, ch = w // cells, h // cells
    for cy in range(cells):
        for cx in range(cells):
            if (cx + cy) % 2 == 0:
                for y in range(cy * ch, (cy + 1) * ch):
                    for x in range(cx * cw, (cx + 1) * cw):
                        pixels[x, y] = (dark, dark, dark)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestCheckImageQuality:
    def test_normal_image_passes(self):
        from app.image_quality import check_image_quality

        result = check_image_quality(_checkerboard_png())
        assert result.ok is True
        assert result.reason == ""

    def test_all_black_image_fails_as_too_dark(self):
        """A photo taken with the lens cap on. Mean luminance ≈ 0."""
        from app.image_quality import check_image_quality

        result = check_image_quality(_png(5))
        assert result.ok is False
        assert "dark" in result.reason.lower()
        # Hints should be actionable, not technical
        assert any("light" in h.lower() or "flash" in h.lower() or "reshoot" in h.lower() for h in result.hints)

    def test_all_white_image_fails_as_too_bright(self):
        """Blown-out exposure or pointing at a lamp. Mean luminance ≈ 255."""
        from app.image_quality import check_image_quality

        result = check_image_quality(_png(250))
        assert result.ok is False
        assert "bright" in result.reason.lower() or "exposed" in result.reason.lower()

    def test_mid_gray_uniform_image_fails_as_low_contrast(self):
        """A photo of a blank wall. Mean luminance fine, stddev ≈ 0."""
        from app.image_quality import check_image_quality

        result = check_image_quality(_png(128))
        assert result.ok is False
        assert "contrast" in result.reason.lower() or "blank" in result.reason.lower()

    def test_dark_but_high_contrast_image_passes(self):
        """A real stout label is mostly black with bright text. Mean
        luminance can be low but stddev is high — the verifier should
        let this through, not reject the agent's legitimate photo."""
        from app.image_quality import check_image_quality

        # Mostly black with a small bright region — like a label with white
        # text on a black background.
        img = Image.new("RGB", (400, 400), (20, 20, 20))
        pixels = img.load()
        # Add bright text-like band in the middle
        for y in range(150, 250):
            for x in range(50, 350):
                pixels[x, y] = (230, 220, 200)
        buf = BytesIO()
        img.save(buf, format="PNG")
        result = check_image_quality(buf.getvalue())
        assert result.ok is True, (
            f"dark+high-contrast image should pass; got reason={result.reason!r}"
        )

    def test_bright_but_high_contrast_image_passes(self):
        """A pale-ale label is mostly bright with dark text. Mirror case
        of the stout — must pass."""
        from app.image_quality import check_image_quality

        img = Image.new("RGB", (400, 400), (240, 235, 220))
        pixels = img.load()
        for y in range(150, 250):
            for x in range(50, 350):
                pixels[x, y] = (15, 15, 15)
        buf = BytesIO()
        img.save(buf, format="PNG")
        result = check_image_quality(buf.getvalue())
        assert result.ok is True

    def test_returns_metrics_in_evidence_dict(self):
        """The evidence dict is what the audit panel surfaces — must
        include the actual luminance + stddev so a reviewer can see
        why the check decided what it did."""
        from app.image_quality import check_image_quality

        result = check_image_quality(_checkerboard_png())
        assert "mean_luminance" in result.evidence
        assert "stddev_luminance" in result.evidence
        assert 0 <= result.evidence["mean_luminance"] <= 255
        assert result.evidence["stddev_luminance"] >= 0

    def test_invalid_image_bytes_fails_gracefully(self):
        """Random bytes (not a real image). The check should fail with an
        actionable reason rather than crashing the route."""
        from app.image_quality import check_image_quality

        result = check_image_quality(b"not actually an image, just random bytes")
        assert result.ok is False
        assert "image" in result.reason.lower() or "decode" in result.reason.lower() or "format" in result.reason.lower()

    def test_jpeg_is_accepted(self):
        """The UI accepts both JPG and PNG; the check must handle both."""
        from app.image_quality import check_image_quality

        img = Image.new("RGB", (400, 400))
        # Re-use the checkerboard pattern, just save as JPEG
        pixels = img.load()
        for y in range(400):
            for x in range(400):
                v = 30 if ((x // 50) + (y // 50)) % 2 == 0 else 220
                pixels[x, y] = (v, v, v)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        result = check_image_quality(buf.getvalue())
        assert result.ok is True

    def test_empty_bytes_fails(self):
        from app.image_quality import check_image_quality

        result = check_image_quality(b"")
        assert result.ok is False
