"""OpenAIExtractor — GPT-4o via the `openai` SDK (async).

Fallback per presearch §3.2. Strongest OCR on degraded images per the
presearch benchmark; Azure OpenAI is the FedRAMP-High production path.

Same prompt + same JSON contract as `GeminiExtractor`. The seam (LabelExtractor
ABC) keeps the verifier provider-agnostic; the only provider-specific code is
the SDK call shape (chat.completions vs google-genai's models.generate_content)
and the data-URL packaging for the image.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.extractors.base import LabelExtractor
from app.extractors.gemini import ExtractorError, _parse_response_text
from app.extractors.prompt import build_extraction_prompt
from app.models import BeverageType, LabelData


class OpenAIExtractor(LabelExtractor):
    """OpenAI GPT-4o implementation of LabelExtractor.

    Constructor takes the SDK `client` (an `openai.AsyncOpenAI`) so tests
    can inject a stub. Use `OpenAIExtractor.from_settings()` in production.
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
    def from_settings(cls) -> "OpenAIExtractor":
        from openai import AsyncOpenAI

        settings = get_settings()
        api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise ExtractorError(
                "OPENAI_API_KEY is empty — cannot build a real OpenAIExtractor"
            )
        client = AsyncOpenAI(api_key=api_key, timeout=settings.extraction_timeout_seconds)
        return cls(
            client=client,
            model=settings.openai_model,
            timeout_seconds=settings.extraction_timeout_seconds,
        )

    async def extract(
        self,
        image_bytes: bytes,
        beverage_type: BeverageType,
        mime_type: str = "image/jpeg",
    ) -> LabelData:
        prompt = build_extraction_prompt(beverage_type)
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{image_b64}"

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
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
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError(
                f"OpenAI call failed: {exc.__class__.__name__}: {exc}"
            ) from exc

        try:
            raw_text = response.choices[0].message.content or ""
        except (AttributeError, IndexError) as exc:
            raise ExtractorError(
                f"OpenAI response had unexpected shape: {exc}"
            ) from exc

        payload = _parse_response_text(raw_text)
        try:
            return LabelData.model_validate(payload)
        except ValidationError as exc:
            raise ExtractorError(
                f"OpenAI response did not match LabelData schema: {exc}"
            ) from exc
