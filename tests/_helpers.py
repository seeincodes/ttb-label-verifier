"""Shared test helpers.

`good_synthetic_png()` is the canonical "image that passes the STR1
image-quality gate" — a 400x400 checkerboard. Every route test that just
needs an opaque blob to throw at multipart upload uses this instead of
the old 32x32 white square (which the quality gate correctly rejects as
"too bright + uniform" — that was the regression triggered when STR1 was
wired in).
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image


def good_synthetic_png(
    size: tuple[int, int] = (400, 400),
    cells: int = 8,
    light: int = 220,
    dark: int = 30,
) -> bytes:
    """8x8 high-contrast checkerboard. Mean ≈ 125, stddev ≈ 95 — clears the
    STR1 gate by a wide margin (well inside both the brightness and contrast
    bands). Production route tests don't care what's on the image because
    they stub the extractor; this just needs to be a *legitimate* image."""
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
