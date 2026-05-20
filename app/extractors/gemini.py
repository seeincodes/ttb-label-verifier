"""GeminiExtractor — Google Gemini 2.5 Flash via the `google-genai` SDK.

Primary extractor per presearch §3.2 (latency, cost, GSA-MAS path). The
implementation is dependency-injected with a `client` so the unit tests can
exercise parsing + error mapping without network. `from_settings()` is the
production factory that wires the real SDK.

JSON-mode quirks the parser defends against (per ERROR_FIX_LOG):
  - String-wrapped JSON (the SDK occasionally double-encodes despite
    `response_mime_type="application/json"`).
  - Markdown code fences leaking through (the prompt forbids them; this is
    belt-and-braces).
  - Schema mismatch — surfaced as `ExtractorError`, not raw ValidationError,
    so the fallback layer catches one class.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.extractors.base import LabelExtractor
from app.extractors.prompt import build_extraction_prompt
from app.models import BeverageType, LabelData


class ExtractorError(Exception):
    """Any failure mapping the vision-model response to a LabelData.

    Caught as one class by the fallback layer (task group 10), regardless of
    whether the underlying cause was a network error, a timeout, malformed
    JSON, or a schema mismatch. The original exception is chained via
    `raise ... from exc` so the audit log retains the root cause.
    """


def _strip_code_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers if present.

    The prompt forbids fences, but a model that ignores instructions
    shouldn't crash the pipeline — strip and parse.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the leading fence (optionally with language tag).
    first_newline = stripped.find("\n")
    if first_newline == -1:
        return stripped
    body = stripped[first_newline + 1 :]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


def _parse_response_text(text: str) -> dict[str, Any]:
    """Robustly parse the model's response text into a JSON dict.

    Handles the known Gemini quirks (string-wrap, code fences) so the caller
    sees a clean `dict` or an `ExtractorError`.
    """
    if not text or not text.strip():
        raise ExtractorError("vision model returned an empty response")

    cleaned = _strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExtractorError(
            f"vision model response was not valid JSON: {exc}"
        ) from exc

    # Gemini JSON-mode quirk: response.text may be a JSON-encoded string of
    # JSON (`"\"{\\\"brand_name\\\":...}\""`). Unwrap once.
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise ExtractorError(
                "vision model returned a string-wrapped non-JSON payload"
            ) from exc

    if not isinstance(parsed, dict):
        raise ExtractorError(
            f"vision model returned a {type(parsed).__name__}, expected a JSON object"
        )

    return parsed


class GeminiExtractor(LabelExtractor):
    """Google Gemini 2.5 Flash implementation of LabelExtractor.

    Constructor takes the SDK `client` so tests can inject a stub. Use
    `GeminiExtractor.from_settings()` in production.
    """

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        timeout_seconds: int,
    ) -> None:
        self._client = client
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> "GeminiExtractor":
        """Build a GeminiExtractor wired to the real `google-genai` SDK.

        Kept out of the unit-test path — instantiating a real `genai.Client`
        in unit tests would either hit the network or fail without a key.
        """
        from google import genai
        from google.genai import types

        settings = get_settings()
        api_key = settings.gemini_api_key.get_secret_value()
        if not api_key:
            raise ExtractorError(
                "GEMINI_API_KEY is empty — cannot build a real GeminiExtractor"
            )
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=settings.extraction_timeout_seconds * 1000
            ),
        )
        return cls(
            client=client,
            model=settings.gemini_model,
            timeout_seconds=settings.extraction_timeout_seconds,
        )

    async def extract(
        self,
        image_bytes: bytes,
        beverage_type: BeverageType,
        mime_type: str = "image/jpeg",
    ) -> LabelData:
        prompt = build_extraction_prompt(beverage_type)
        image_part = _image_part(image_bytes, mime_type)

        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=[image_part, prompt],
                config=_json_config(),
            )
        except ExtractorError:
            raise
        except Exception as exc:  # SDK timeout, 5xx, 429, etc.
            raise ExtractorError(
                f"vision model call failed: {exc.__class__.__name__}: {exc}"
            ) from exc

        raw_text = getattr(response, "text", None) or ""
        payload = _parse_response_text(raw_text)
        try:
            return LabelData.model_validate(payload)
        except ValidationError as exc:
            raise ExtractorError(
                f"vision model response did not match LabelData schema: {exc}"
            ) from exc


def _image_part(image_bytes: bytes, mime_type: str) -> Any:
    """Build the SDK's image-part object.

    Importing `google.genai.types` is conditional so unit tests don't need
    the SDK installed to exercise parsing paths — tests pass plain bytes via
    the fake client and never reach this helper. The real SDK call always
    reaches it.
    """
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover — real env always has it
        raise ExtractorError("google-genai SDK is not installed") from exc
    return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)


def _json_config() -> Any:
    """Build the JSON-mode generation config. Same conditional-import note
    as `_image_part` — kept out of the hot path so tests don't need the SDK."""
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover
        raise ExtractorError("google-genai SDK is not installed") from exc
    return types.GenerateContentConfig(response_mime_type="application/json")
