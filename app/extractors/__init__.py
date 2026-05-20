"""Extractor package — factory + fallback wrapper.

`build_extractor()` is the single public entry point used by the FastAPI
dependency-injection layer (`app.dependencies.get_extractor`). It reads
`EXTRACTOR_PROVIDER` from settings, builds the appropriate primary, and
wraps it in a `FallbackExtractor` whose secondary is the other provider —
per MVP7 the automatic fallback retries once on a transient
`ExtractorError`.

If the secondary's API key isn't configured, the factory returns a bare
primary (single-provider mode) so a missing fallback key doesn't break
the primary path.
"""
from __future__ import annotations

from typing import Optional

from app.config import get_settings
from app.extractors.base import ExtractionAudit, LabelExtractor
from app.extractors.gemini import ExtractorError, GeminiExtractor
from app.extractors.openai import OpenAIExtractor
from app.models import BeverageType, LabelData


class FallbackExtractor(LabelExtractor):
    """Wraps a primary `LabelExtractor` with a secondary fallback.

    On a primary `ExtractorError` (which includes timeouts, 5xx, schema
    mismatch, and the JSON-mode quirks the parser catches), retries once
    with the secondary. The audit surface (`extract_with_audit`) reports
    whether the fallback was used — `_run_verification` reads this and
    sets `fallback_used` on the `VerificationResult` for the audit panel.

    If `secondary is None`, the wrapper degenerates to a transparent
    pass-through over the primary — supports the single-provider deploy.
    """

    def __init__(
        self,
        *,
        primary: LabelExtractor,
        secondary: Optional[LabelExtractor],
    ) -> None:
        self.primary = primary
        self.secondary = secondary

    async def extract(
        self,
        image_bytes: bytes,
        beverage_type: BeverageType,
        mime_type: str = "image/jpeg",
    ) -> LabelData:
        label, _audit = await self.extract_with_audit(
            image_bytes, beverage_type, mime_type
        )
        return label

    async def extract_with_audit(
        self,
        image_bytes: bytes,
        beverage_type: BeverageType,
        mime_type: str = "image/jpeg",
    ) -> tuple[LabelData, ExtractionAudit]:
        try:
            label = await self.primary.extract(image_bytes, beverage_type, mime_type)
            return label, ExtractionAudit(
                fallback_used=False,
                provider_used=self.primary.__class__.__name__,
            )
        except ExtractorError as primary_exc:
            if self.secondary is None:
                # No fallback configured — propagate so the route surfaces it.
                raise
            try:
                label = await self.secondary.extract(image_bytes, beverage_type, mime_type)
            except ExtractorError as secondary_exc:
                # Both failed — preserve both messages for the audit log.
                raise ExtractorError(
                    f"primary ({self.primary.__class__.__name__}) failed: "
                    f"{primary_exc} | secondary ({self.secondary.__class__.__name__}) "
                    f"also failed: {secondary_exc}"
                ) from secondary_exc
            return label, ExtractionAudit(
                fallback_used=True,
                provider_used=self.secondary.__class__.__name__,
            )


def build_extractor() -> LabelExtractor:
    """Construct the production extractor per `EXTRACTOR_PROVIDER`.

    If both API keys are configured, returns a `FallbackExtractor` with the
    chosen provider as primary and the other as secondary (MVP7 automatic
    fallback). If only the primary key is configured, returns a bare
    extractor so a missing fallback key doesn't break the primary path.

    Raises `ExtractorError` if the primary's key is missing — the prototype
    can't run without at least one working provider.
    """
    settings = get_settings()
    gemini_key = settings.gemini_api_key.get_secret_value()
    openai_key = settings.openai_api_key.get_secret_value()

    if settings.extractor_provider == "openai":
        if not openai_key:
            raise ExtractorError(
                "EXTRACTOR_PROVIDER=openai but OPENAI_API_KEY is empty"
            )
        primary: LabelExtractor = OpenAIExtractor.from_settings()
        secondary = GeminiExtractor.from_settings() if gemini_key else None
    else:  # gemini (default)
        if not gemini_key:
            raise ExtractorError(
                "EXTRACTOR_PROVIDER=gemini but GEMINI_API_KEY is empty"
            )
        primary = GeminiExtractor.from_settings()
        secondary = OpenAIExtractor.from_settings() if openai_key else None

    return FallbackExtractor(primary=primary, secondary=secondary)


__all__ = ["FallbackExtractor", "build_extractor"]
