"""Smoke test for the Gemini vision SDK.

Verifies that:
  1. GEMINI_API_KEY is loadable from .env
  2. google-genai can call the configured model
  3. The response parses as text

Usage: `make smoke-gemini` (or `python scripts/smoke_gemini.py`)
Optional: pass a path to a real label image as the first argument.
If no argument is given, a synthetic 32x32 white PNG is used.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types

# Allow `python scripts/smoke_gemini.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

# 32x32 white PNG (generated once, base64-encoded for portability).
SYNTHETIC_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgAQMAAABJtOi3AAAABlBMVEX///8AAA"
    "BVwtN+AAAAEElEQVR4nGNgGAWjYBSMAggAAQEAAAGYG3SXAAAAAElFTkSuQmCC"
)


def main() -> int:
    settings = get_settings()
    api_key = settings.gemini_api_key.get_secret_value()
    if not api_key:
        print("ERROR: GEMINI_API_KEY is empty. Set it in .env and retry.", file=sys.stderr)
        return 2

    if len(sys.argv) > 1:
        image_path = Path(sys.argv[1])
        if not image_path.exists():
            print(f"ERROR: image not found: {image_path}", file=sys.stderr)
            return 2
        image_bytes = image_path.read_bytes()
        mime_type = "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        print(f"Using image: {image_path} ({len(image_bytes)} bytes, {mime_type})")
    else:
        image_bytes = base64.b64decode(SYNTHETIC_PNG_B64)
        mime_type = "image/png"
        print(f"Using synthetic 32x32 white PNG ({len(image_bytes)} bytes)")

    print(f"Model: {settings.gemini_model}")
    print(f"Timeout: {settings.extraction_timeout_seconds}s")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=settings.extraction_timeout_seconds * 1000),
    )

    prompt = (
        "You are a smoke test. Respond with valid JSON only, no markdown. "
        'Schema: {"saw_image": boolean, "description": "one short sentence"}'
    )

    started = time.perf_counter()
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    raw = response.text or ""
    print(f"\nLatency: {elapsed_ms:.0f}ms")
    print(f"Raw response: {raw!r}")

    try:
        parsed = json.loads(raw)
        print(f"Parsed JSON: {json.dumps(parsed, indent=2)}")
        print("\nOK — Gemini extractor is reachable and returned JSON.")
        return 0
    except json.JSONDecodeError as exc:
        print(f"WARN: response was not valid JSON ({exc}). SDK reachable but prompt may need tuning.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
