"""End-to-end smoke test for the GeminiExtractor.

Unlike scripts/smoke_gemini.py (which tests the bare SDK), this drives the
real `app.extractors.gemini.GeminiExtractor` — prompt builder, SDK call,
JSON-mode unwrap, and Pydantic schema validation — against one image.

Usage:
    python scripts/smoke_extractor.py                            # synthetic PNG
    python scripts/smoke_extractor.py path/to/label.jpg          # real label
    python scripts/smoke_extractor.py path/to/label.jpg wine     # override
                                                                 # beverage type

Exit codes:
    0 — Gemini reachable, response parsed into a valid LabelData
    1 — call succeeded but the response did not match the §5.5 contract
    2 — config error (missing key, bad image path)

The synthetic-PNG path is a wiring check: a blank 32x32 image won't yield
meaningful fields, but a healthy run returns the canonical §5.5 shape with
every value=null + confidence=low, which is what the prompt instructs.
A failure here points at the SDK / prompt / parser glue, not at the model.

For the task list's "manual test on 3 sample images" requirement, run this
against the three labels that land in sample_data/ as part of task group 6:
    python scripts/smoke_extractor.py sample_data/spirits-pass.jpg distilled_spirits
    python scripts/smoke_extractor.py sample_data/abv-fail.jpg     distilled_spirits
    python scripts/smoke_extractor.py sample_data/warning-fail.jpg distilled_spirits
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

# Allow `python scripts/smoke_extractor.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extractors import build_extractor  # noqa: E402
from app.extractors.gemini import ExtractorError  # noqa: E402
from app.models import BeverageType  # noqa: E402

# 32x32 white PNG, base64-encoded for portability (same as smoke_gemini.py).
SYNTHETIC_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgAQMAAABJtOi3AAAABlBMVEX///8AAA"
    "BVwtN+AAAAEElEQVR4nGNgGAWjYBSMAggAAQEAAAGYG3SXAAAAAElFTkSuQmCC"
)


def _load_image(argv: list[str]) -> tuple[bytes, str, str]:
    """Return (image_bytes, mime_type, label) given argv slice."""
    if len(argv) >= 1:
        path = Path(argv[0])
        if not path.exists():
            print(f"ERROR: image not found: {path}", file=sys.stderr)
            sys.exit(2)
        suffix = path.suffix.lower()
        mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
        return path.read_bytes(), mime, str(path)
    return base64.b64decode(SYNTHETIC_PNG_B64), "image/png", "<synthetic 32x32 PNG>"


def _resolve_beverage_type(argv: list[str]) -> BeverageType:
    if len(argv) >= 2:
        try:
            return BeverageType(argv[1])
        except ValueError:
            valid = ", ".join(b.value for b in BeverageType)
            print(
                f"ERROR: unknown beverage type {argv[1]!r}. "
                f"Valid: {valid}",
                file=sys.stderr,
            )
            sys.exit(2)
    return BeverageType.DISTILLED_SPIRITS


async def main() -> int:
    image_bytes, mime, label = _load_image(sys.argv[1:])
    beverage = _resolve_beverage_type(sys.argv[1:])
    print(f"Image:        {label} ({len(image_bytes)} bytes, {mime})")
    print(f"Beverage:     {beverage.value}")

    try:
        extractor = build_extractor()
    except ExtractorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # FallbackExtractor exposes .primary; bare extractors expose .model directly.
    inner = getattr(extractor, "primary", extractor)
    print(f"Primary:      {inner.__class__.__name__} ({getattr(inner, 'model', '?')})")
    secondary = getattr(extractor, "secondary", None)
    if secondary is not None:
        print(
            f"Secondary:    {secondary.__class__.__name__} "
            f"({getattr(secondary, 'model', '?')})  (used on primary ExtractorError)"
        )
    print(f"Timeout:      {getattr(inner, 'timeout_seconds', '?')}s")
    print()

    started = time.perf_counter()
    try:
        result, audit = await extractor.extract_with_audit(image_bytes, beverage, mime)
    except ExtractorError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"Latency:      {elapsed_ms:.0f}ms")
        print(f"FAIL: extractor raised: {exc}", file=sys.stderr)
        cause = exc.__cause__
        if cause is not None:
            print(f"      cause:    {cause.__class__.__name__}: {cause}", file=sys.stderr)
        return 1

    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"Latency:      {elapsed_ms:.0f}ms")
    print(f"Provider:     {audit.provider_used}  (fallback={audit.fallback_used})")
    print()
    print(f"OK — {audit.provider_used} reachable, response matched LabelData (§5.5).")
    print()
    print(json.dumps(result.model_dump(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
