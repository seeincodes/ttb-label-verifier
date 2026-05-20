"""Tests for app.extractors.gemini — GeminiExtractor.

The extractor is dependency-injected with a `client` object so these tests
exercise the parse / error-mapping paths without network. The integration
smoke test (`scripts/smoke_gemini.py`) covers the real SDK call.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.models import BeverageType, LabelData


GEMINI_SAMPLE_JSON = {
    "brand_name": {"value": "OLD TOM DISTILLERY", "confidence": "high"},
    "class_type": {
        "value": "Kentucky Straight Bourbon Whiskey",
        "confidence": "high",
    },
    "alcohol_content_pct": {"value": 45.0, "confidence": "high"},
    "alcohol_content_text": {
        "value": "45% ALC./VOL. (90 PROOF)",
        "confidence": "high",
    },
    "net_contents": {"value": "750 mL", "confidence": "high"},
    "bottler_name": {"value": "Old Tom Distillery LLC", "confidence": "high"},
    "bottler_address": {
        "value": "123 Distillery Rd, Frankfort, KY",
        "confidence": "medium",
    },
    "country_of_origin": {"value": None, "confidence": "high"},
    "government_warning_text": {
        "value": "GOVERNMENT WARNING: ...",
        "confidence": "high",
    },
    "government_warning_formatting": {
        "caps_correct": True,
        "bold_correct": True,
        "continuous": True,
        "confidence": "high",
    },
}


# A minimal stand-in for the `google-genai` response object: it just needs a
# `.text` attribute. The real SDK returns more, but `extract` only reads .text.
@dataclass
class FakeResponse:
    text: str


class FakeAioModels:
    """Stand-in for `client.aio.models`. Records the last call and returns
    a pre-canned response. Async so we exercise the real awaited path."""

    def __init__(self, response: Any):
        self.response = response
        self.last_call: dict[str, Any] | None = None

    async def generate_content(self, **kwargs: Any) -> Any:
        self.last_call = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeAio:
    def __init__(self, response: Any):
        self.models = FakeAioModels(response)


class FakeClient:
    def __init__(self, response: Any):
        self.aio = FakeAio(response)


def _make_extractor(response: Any):
    from app.extractors.gemini import GeminiExtractor

    client = FakeClient(response)
    extractor = GeminiExtractor(
        client=client, model="gemini-2.5-flash", timeout_seconds=12
    )
    return extractor, client


class TestGeminiExtractorConstruction:
    def test_stores_model_and_timeout(self):
        from app.extractors.gemini import GeminiExtractor

        client = FakeClient(FakeResponse(text="{}"))
        ex = GeminiExtractor(
            client=client, model="gemini-2.5-flash", timeout_seconds=12
        )
        assert ex.model == "gemini-2.5-flash"
        assert ex.timeout_seconds == 12

    def test_is_a_label_extractor(self):
        """Substitutability check — the factory in app.extractors.__init__
        must be able to return a GeminiExtractor where a LabelExtractor is
        expected."""
        from app.extractors.base import LabelExtractor
        from app.extractors.gemini import GeminiExtractor

        assert issubclass(GeminiExtractor, LabelExtractor)


class TestGeminiExtractorHappyPath:
    def test_parses_valid_json_response(self):
        ex, _ = _make_extractor(FakeResponse(text=json.dumps(GEMINI_SAMPLE_JSON)))
        result = asyncio.run(
            ex.extract(b"\x89PNG", BeverageType.DISTILLED_SPIRITS, "image/png")
        )

        assert isinstance(result, LabelData)
        assert result.brand_name.value == "OLD TOM DISTILLERY"
        assert result.government_warning_formatting.caps_correct is True

    def test_passes_prompt_image_and_model_to_sdk(self):
        """The SDK call must carry: the configured model, a `contents` payload
        with the image bytes AND the prompt, and a JSON-mime config. If any
        of these are wrong, the real call would fail in a hard-to-debug way."""
        ex, client = _make_extractor(
            FakeResponse(text=json.dumps(GEMINI_SAMPLE_JSON))
        )
        asyncio.run(
            ex.extract(
                b"raw-jpeg-bytes",
                BeverageType.WINE,
                "image/jpeg",
            )
        )
        call = client.aio.models.last_call
        assert call is not None
        assert call["model"] == "gemini-2.5-flash"
        # `contents` is a list; one item carries the image part, one is text.
        contents = call["contents"]
        assert isinstance(contents, list)
        assert len(contents) >= 2

        # At least one element should expose the image bytes; at least one
        # should be the prompt string mentioning 'wine'.
        text_items = [c for c in contents if isinstance(c, str)]
        assert any("wine" in t.lower() for t in text_items), (
            "prompt body for wine label not found in SDK contents"
        )

    def test_passes_correct_mime_type(self):
        """The Gemini SDK sends image MIME via the Part.from_bytes call. We
        can't introspect the Part easily, so we check the SDK call carries
        non-text items (representing the image part)."""
        ex, client = _make_extractor(
            FakeResponse(text=json.dumps(GEMINI_SAMPLE_JSON))
        )
        asyncio.run(
            ex.extract(b"raw-png", BeverageType.OTHER, "image/png")
        )
        call = client.aio.models.last_call
        non_text = [c for c in call["contents"] if not isinstance(c, str)]
        assert len(non_text) >= 1


class TestGeminiExtractorJsonModeQuirk:
    """Per ERROR_FIX_LOG entry on Gemini JSON-mode: even with
    response_mime_type=application/json the SDK may return a string-wrapped
    JSON object (response.text is a JSON-encoded string of JSON). The
    extractor must unwrap defensively."""

    def test_handles_string_wrapped_json(self):
        wrapped = json.dumps(json.dumps(GEMINI_SAMPLE_JSON))  # double-encoded
        ex, _ = _make_extractor(FakeResponse(text=wrapped))
        result = asyncio.run(
            ex.extract(b"\x89PNG", BeverageType.DISTILLED_SPIRITS, "image/png")
        )
        assert result.brand_name.value == "OLD TOM DISTILLERY"

    def test_strips_markdown_code_fences_if_present(self):
        """Belt-and-braces. The prompt forbids code fences, but if a model
        ships them anyway, the extractor must not crash — strip and parse."""
        fenced = "```json\n" + json.dumps(GEMINI_SAMPLE_JSON) + "\n```"
        ex, _ = _make_extractor(FakeResponse(text=fenced))
        result = asyncio.run(
            ex.extract(b"\x89PNG", BeverageType.DISTILLED_SPIRITS, "image/png")
        )
        assert result.brand_name.value == "OLD TOM DISTILLERY"


class TestGeminiExtractorErrors:
    def test_raises_on_invalid_json(self):
        from app.extractors.gemini import ExtractorError

        ex, _ = _make_extractor(FakeResponse(text="not valid json"))
        with pytest.raises(ExtractorError) as exc_info:
            asyncio.run(
                ex.extract(b"\x00", BeverageType.DISTILLED_SPIRITS, "image/png")
            )
        # the message should hint at what failed so the fallback layer logs are useful
        assert "json" in str(exc_info.value).lower()

    def test_raises_on_schema_mismatch(self):
        """A response that *is* JSON but missing required §5.5 keys is just
        as broken — Pydantic's ValidationError should surface as ExtractorError."""
        from app.extractors.gemini import ExtractorError

        ex, _ = _make_extractor(
            FakeResponse(text='{"brand_name": {"value": "x", "confidence": "high"}}')
        )
        with pytest.raises(ExtractorError):
            asyncio.run(
                ex.extract(b"\x00", BeverageType.DISTILLED_SPIRITS, "image/png")
            )

    def test_raises_on_empty_response(self):
        from app.extractors.gemini import ExtractorError

        ex, _ = _make_extractor(FakeResponse(text=""))
        with pytest.raises(ExtractorError):
            asyncio.run(
                ex.extract(b"\x00", BeverageType.DISTILLED_SPIRITS, "image/png")
            )

    def test_sdk_exception_is_wrapped(self):
        """If the underlying SDK raises (timeout, 5xx, rate-limit), the
        extractor surfaces it as ExtractorError. The fallback layer in
        task group 10 catches one class, not three vendor-specific ones."""
        from app.extractors.gemini import ExtractorError

        class FakeSdkError(Exception):
            pass

        ex, _ = _make_extractor(FakeSdkError("503 service unavailable"))
        with pytest.raises(ExtractorError) as exc_info:
            asyncio.run(
                ex.extract(b"\x00", BeverageType.DISTILLED_SPIRITS, "image/png")
            )
        # Original cause is chained so we can see the underlying failure
        assert exc_info.value.__cause__ is not None
