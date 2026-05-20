"""Smoke test for the OpenAI vision SDK.

Verifies that:
  1. OPENAI_API_KEY is loadable from .env
  2. The openai SDK can call the configured model (gpt-4o by default)
  3. The response parses as text

Usage: `make smoke-openai` (or `python scripts/smoke_openai.py`)
Optional: pass a path to a real label image as the first argument.
If no argument is given, a synthetic 32x32 white PNG is used.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

from openai import APITimeoutError, AuthenticationError, OpenAI, RateLimitError

# Allow `python scripts/smoke_openai.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

# 32x32 white PNG (same payload as smoke_gemini.py for parity).
SYNTHETIC_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgAQMAAABJtOi3AAAABlBMVEX///8AAA"
    "BVwtN+AAAAEElEQVR4nGNgGAWjYBSMAggAAQEAAAGYG3SXAAAAAElFTkSuQmCC"
)


def main() -> int:
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key:
        print("ERROR: OPENAI_API_KEY is empty. Set it in .env and retry.", file=sys.stderr)
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

    print(f"Model: {settings.openai_model}")
    print(f"Timeout: {settings.extraction_timeout_seconds}s")

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{image_b64}"

    client = OpenAI(api_key=api_key, timeout=settings.extraction_timeout_seconds)

    prompt = (
        "You are a smoke test. Respond with valid JSON only, no markdown. "
        'Schema: {"saw_image": boolean, "description": "one short sentence"}'
    )

    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
    except APITimeoutError:
        print(f"ERROR: request timed out after {settings.extraction_timeout_seconds}s", file=sys.stderr)
        return 1
    except AuthenticationError as exc:
        print(f"ERROR: OPENAI_API_KEY rejected ({exc.message}). Check the key in .env.", file=sys.stderr)
        return 2
    except RateLimitError as exc:
        # OpenAI returns 429 for both quota exhaustion (terminal, billing issue)
        # and rate limiting (retryable). exc.code carries the OpenAI error code.
        if getattr(exc, "code", None) == "insufficient_quota":
            print(
                "ERROR: OpenAI account has no available quota (429 insufficient_quota).\n"
                "       This is an account/billing issue, not a code bug. Add billing\n"
                "       at https://platform.openai.com/account/billing and retry.",
                file=sys.stderr,
            )
            return 3
        print(f"ERROR: rate limit hit ({exc.message}). Retry after backoff.", file=sys.stderr)
        return 4
    elapsed_ms = (time.perf_counter() - started) * 1000

    raw = response.choices[0].message.content or ""
    print(f"\nLatency: {elapsed_ms:.0f}ms")
    print(f"Raw response: {raw!r}")

    try:
        parsed = json.loads(raw)
        print(f"Parsed JSON: {json.dumps(parsed, indent=2)}")
        print("\nOK — OpenAI extractor is reachable and returned JSON.")
        return 0
    except json.JSONDecodeError as exc:
        print(f"WARN: response was not valid JSON ({exc}). SDK reachable but prompt may need tuning.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
