"""Classical-CV pre-check for uploaded images (STR1).

Runs *before* the Gemini call so an obviously-unreadable photo doesn't burn
1.5–7 s of model latency just to come back as ERROR via the MVP9
confidence gate. The check is deliberately permissive — only photos that
are clearly unusable (lens-cap-on dark, blown-out white, blank wall) fail.
A real stout label is mostly black with bright text; its mean luminance is
low but its standard deviation is high. The contrast check distinguishes
these two cases.

Heuristics (Pillow-only, no extra dependency):
  - Decode image, convert to luma channel ("L" mode).
  - mean < 30  AND  stddev < 25       → too dark, reshoot with more light
  - mean > 225 AND  stddev < 25       → too bright / over-exposed
  - stddev < 12 (regardless of mean)  → blank wall / out-of-focus
  - decode failure                    → "looks like the file isn't an image
                                          or is corrupted"

The thresholds are calibrated for the synthetic test fixtures and validated
on the manual_test/batch labels — every Fox-Hollow / Crescent-Bay /
Valley-Springs PNG passes. Tune via tests/test_image_quality.py if real
labels start being rejected.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError


# Calibrated against the synthetic fixtures in tests/test_image_quality.py
# and the manual_test/batch real-style labels. Bumping these tighter risks
# false positives on legitimate dark stout / pale lager labels.
_DARK_MEAN_THRESHOLD = 30.0
_BRIGHT_MEAN_THRESHOLD = 225.0
_UNIFORM_STDDEV_THRESHOLD = 25.0   # below this AND mean extreme → fail
_BLANK_STDDEV_THRESHOLD = 12.0     # below this regardless of mean → fail


@dataclass(frozen=True)
class ImageQualityResult:
    """Outcome of the classical-CV pre-check.

    ok=True means "good enough to send to the vision model." False means
    we should short-circuit with an actionable reshoot reason. Evidence
    carries the actual measurements so the audit panel can show *why*
    the check decided what it did — important when a real agent's photo
    is rejected and they need to understand the threshold.
    """

    ok: bool
    reason: str = ""
    hints: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


def check_image_quality(image_bytes: bytes) -> ImageQualityResult:
    """Run the pre-check on raw uploaded image bytes.

    Never raises. A decode failure is just another kind of `ok=False`
    so the route layer can use the same `_error_panel.html` rendering
    path for every pre-check failure.
    """
    if not image_bytes:
        return ImageQualityResult(
            ok=False,
            reason="The uploaded file is empty.",
            hints=["Pick the label image again."],
            evidence={"size_bytes": 0},
        )

    try:
        img = Image.open(BytesIO(image_bytes))
        img.load()  # force-decode now so we can catch errors before measuring
        luma = img.convert("L")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return ImageQualityResult(
            ok=False,
            reason="The file doesn't look like a readable image (JPG or PNG).",
            hints=[
                "Confirm the file is actually a JPG or PNG.",
                "Re-export from the photo app if you saved a HEIC or webp.",
            ],
            evidence={"decode_error": f"{exc.__class__.__name__}: {exc}"},
        )

    pixels = list(luma.getdata())
    if not pixels:
        return ImageQualityResult(
            ok=False,
            reason="The image is empty (no pixels).",
            hints=["Pick a different image."],
            evidence={"pixel_count": 0},
        )

    mean = sum(pixels) / len(pixels)
    stddev = statistics.pstdev(pixels)
    evidence = {
        "mean_luminance": round(mean, 2),
        "stddev_luminance": round(stddev, 2),
        "pixel_count": len(pixels),
        "size": list(luma.size),
    }

    # Lens-cap / very dim photo: dark AND uniformly so (no bright text
    # rescuing the contrast). Checked before the generic low-contrast branch
    # so the reshoot hint is "use more light," not the less-actionable
    # "image has no contrast."
    if mean < _DARK_MEAN_THRESHOLD and stddev < _UNIFORM_STDDEV_THRESHOLD:
        return ImageQualityResult(
            ok=False,
            reason=(
                f"The image is very dark and almost uniform "
                f"(mean luminance {mean:.1f}/255, stddev {stddev:.1f}/255) — "
                "details probably aren't readable."
            ),
            hints=[
                "Add more light or move under a lamp.",
                "If you're using flash, angle the bottle to avoid glare on the label.",
                "Reshoot with the label closer to the camera.",
            ],
            evidence=evidence,
        )

    # Blown-out exposure: bright AND uniformly so.
    if mean > _BRIGHT_MEAN_THRESHOLD and stddev < _UNIFORM_STDDEV_THRESHOLD:
        return ImageQualityResult(
            ok=False,
            reason=(
                f"The image is very bright and almost uniform "
                f"(mean luminance {mean:.1f}/255, stddev {stddev:.1f}/255) — "
                "the label text is probably washed out."
            ),
            hints=[
                "Reduce the lighting or step away from a bright lamp / window.",
                "Tap the label on screen to lower the camera's exposure.",
            ],
            evidence=evidence,
        )

    # Blank / featureless image (mid-gray wall, completely-out-of-focus shot)
    # whose mean luminance is neither extreme. Catches the "took a photo of
    # the desk by accident" case.
    if stddev < _BLANK_STDDEV_THRESHOLD:
        return ImageQualityResult(
            ok=False,
            reason=(
                f"The image has almost no contrast (stddev {stddev:.1f}/255) — "
                "looks like a blank wall or a very out-of-focus shot."
            ),
            hints=[
                "Make sure the label fills most of the frame.",
                "Tap the label on screen to refocus before taking the photo.",
            ],
            evidence=evidence,
        )

    return ImageQualityResult(ok=True, evidence=evidence)
