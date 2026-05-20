"""Tests for app.extractors.openai — OpenAIExtractor.

Same DI pattern as GeminiExtractor: the constructor takes a stub client
so tests exercise parse + error-mapping without burning OpenAI quota
(which has a known billing-quota issue per docs/ERROR_FIX_LOG.md).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.models import BeverageType, LabelData


GPT4O_SAMPLE_JSON = {
    "brand_name":           {"value": "OLD TOM DISTILLERY", "confidence": "high"},
    "class_type":           {"value": "Kentucky Straight Bourbon Whiskey", "confidence": "high"},
    "alcohol_content_pct":  {"value": 45.0, "confidence": "high"},
    "alcohol_content_text": {"value": "45% ALC./VOL.", "confidence": "high"},
    "net_contents":         {"value": "750 mL", "confidence": "high"},
    "bottler_name":         {"value": "Old Tom Distillery LLC", "confidence": "high"},
    "bottler_address":      {"value": "123 Distillery Rd", "confidence": "high"},
    "country_of_origin":    {"value": None, "confidence": "high"},
    "government_warning_text": {
        "value": "GOVERNMENT WARNING: ...", "confidence": "high"
    },
    "government_warning_formatting": {
        "caps_correct": True,
        "bold_correct": True,
        "continuous":   True,
        "confidence":   "high"
    }
}


# Minimal OpenAI-shaped fake response object: chat.completions.create returns
# something with `.choices[0].message.content`. We replicate that shape.
@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeChatResponse:
    choices: list[FakeChoice]


class FakeAsyncChatCompletions:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.last_call: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.last_call = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeAsyncChat:
    def __init__(self, response: Any) -> None:
        self.completions = FakeAsyncChatCompletions(response)


class FakeAsyncOpenAI:
    """Mirrors `openai.AsyncOpenAI` shape minimally enough for the extractor.

    The real client exposes `client.chat.completions.create(...)`. We mirror
    that path; the extractor never touches anything else."""

    def __init__(self, response: Any) -> None:
        self.chat = FakeAsyncChat(response)


def _make_response(payload: Any) -> FakeChatResponse:
    """Build a FakeChatResponse whose .choices[0].message.content == payload."""
    return FakeChatResponse(choices=[FakeChoice(message=FakeMessage(content=payload))])


def _make_extractor(response: Any):
    from app.extractors.openai import OpenAIExtractor

    client = FakeAsyncOpenAI(response)
    extractor = OpenAIExtractor(client=client, model="gpt-4o", timeout_seconds=12)
    return extractor, client


class TestOpenAIExtractorConstruction:
    def test_stores_model_and_timeout(self):
        from app.extractors.openai import OpenAIExtractor

        client = FakeAsyncOpenAI(_make_response("{}"))
        ex = OpenAIExtractor(client=client, model="gpt-4o", timeout_seconds=12)
        assert ex.model == "gpt-4o"
        assert ex.timeout_seconds == 12

    def test_is_a_label_extractor(self):
        from app.extractors.base import LabelExtractor
        from app.extractors.openai import OpenAIExtractor

        assert issubclass(OpenAIExtractor, LabelExtractor)


class TestOpenAIExtractorHappyPath:
    def test_parses_valid_json_response(self):
        ex, _ = _make_extractor(_make_response(json.dumps(GPT4O_SAMPLE_JSON)))
        result = asyncio.run(
            ex.extract(b"\x89PNG", BeverageType.DISTILLED_SPIRITS, "image/png")
        )
        assert isinstance(result, LabelData)
        assert result.brand_name.value == "OLD TOM DISTILLERY"

    def test_sends_image_as_data_url_with_correct_mime(self):
        """OpenAI Chat Completions vision expects image_url with a data: URL.
        The extractor must construct it with the right MIME type and base64."""
        ex, client = _make_extractor(_make_response(json.dumps(GPT4O_SAMPLE_JSON)))
        asyncio.run(
            ex.extract(b"hello", BeverageType.WINE, "image/png")
        )
        call = client.chat.completions.last_call
        assert call is not None
        # messages[0].content is a list of {type, text} / {type, image_url}
        # blocks. Find the image block.
        messages = call["messages"]
        blocks = messages[0]["content"]
        image_blocks = [b for b in blocks if b.get("type") == "image_url"]
        assert len(image_blocks) == 1
        url = image_blocks[0]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        # text block contains the wine-flavored prompt
        text_blocks = [b for b in blocks if b.get("type") == "text"]
        assert any("wine" in b["text"].lower() for b in text_blocks)

    def test_requests_json_response_format(self):
        """`response_format={"type": "json_object"}` is the JSON-mode hint
        for OpenAI; without it the model can return wrapped text."""
        ex, client = _make_extractor(_make_response(json.dumps(GPT4O_SAMPLE_JSON)))
        asyncio.run(
            ex.extract(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
        )
        assert client.chat.completions.last_call.get("response_format") == {"type": "json_object"}

    def test_sends_configured_model(self):
        ex, client = _make_extractor(_make_response(json.dumps(GPT4O_SAMPLE_JSON)))
        asyncio.run(ex.extract(b"x", BeverageType.OTHER, "image/png"))
        assert client.chat.completions.last_call["model"] == "gpt-4o"


class TestOpenAIExtractorErrors:
    def test_raises_extractor_error_on_invalid_json(self):
        from app.extractors.gemini import ExtractorError

        ex, _ = _make_extractor(_make_response("not json at all"))
        with pytest.raises(ExtractorError):
            asyncio.run(
                ex.extract(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
            )

    def test_raises_extractor_error_on_schema_mismatch(self):
        from app.extractors.gemini import ExtractorError

        ex, _ = _make_extractor(
            _make_response('{"brand_name": {"value": "x", "confidence": "high"}}')
        )
        with pytest.raises(ExtractorError):
            asyncio.run(
                ex.extract(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
            )

    def test_raises_extractor_error_on_empty_content(self):
        from app.extractors.gemini import ExtractorError

        ex, _ = _make_extractor(_make_response(""))
        with pytest.raises(ExtractorError):
            asyncio.run(
                ex.extract(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
            )

    def test_sdk_exception_wrapped(self):
        from app.extractors.gemini import ExtractorError

        class FakeSdkError(Exception):
            pass

        ex, _ = _make_extractor(FakeSdkError("rate limit"))
        with pytest.raises(ExtractorError) as exc_info:
            asyncio.run(
                ex.extract(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
            )
        assert exc_info.value.__cause__ is not None
