"""LabelExtractor ABC — the single abstraction the verifier sees.

Every concrete provider (Gemini, OpenAI, future Claude / Bedrock) implements
`extract` and returns the same `LabelData` shape (presearch §5.5). The
verifier therefore never reaches into provider-specific JSON; switching
providers is a one-line factory change.

The ABC is intentionally tiny. The writeup §10 calls out rolling this
rather than depending on LiteLLM / LangChain as deliberate architectural
signal — keep it that way.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models import BeverageType, LabelData


@dataclass(frozen=True)
class ExtractionAudit:
    """Side-channel info from an extraction call.

    Most extractors return `fallback_used=False, provider=<their name>`.
    The `FallbackExtractor` wrapper uses this to surface "primary failed
    and we recovered" to the caller without breaking the `extract` ABC.
    """

    fallback_used: bool
    provider_used: str


class LabelExtractor(ABC):
    """Vision-AI label extractor.

    Implementations send `image_bytes` (with the right MIME type) to a
    vision model and return a `LabelData` conforming to the presearch
    §5.5 contract — including per-field confidence so the verifier's
    confidence gate (MVP9) has the signal it needs.
    """

    @abstractmethod
    async def extract(
        self,
        image_bytes: bytes,
        beverage_type: BeverageType,
        mime_type: str = "image/jpeg",
    ) -> LabelData:
        """Extract structured label fields from one image.

        Args:
            image_bytes: raw image content (JPG or PNG).
            beverage_type: drives prompt conditionality (presearch §5.6) so
                the model knows which fields are required for this label.
            mime_type: explicit Content-Type for the vendor SDK; missing
                MIME has historically manifested as opaque 400s.

        Returns:
            LabelData with per-field confidence. The verifier maps any
            required field at `low` confidence to verdict ERROR rather
            than risking a false PASS / FAIL.
        """

    async def extract_with_audit(
        self,
        image_bytes: bytes,
        beverage_type: BeverageType,
        mime_type: str = "image/jpeg",
    ) -> tuple[LabelData, ExtractionAudit]:
        """Extract and return audit metadata alongside.

        Default implementation calls `extract` and reports
        `fallback_used=False, provider_used=<class name>`. The
        `FallbackExtractor` overrides this to surface when it had to
        retry with the secondary provider.

        Routes that care about fallback (the result-panel header in
        single-label, the result row in batch) call this; callers that
        don't care use `extract` directly.
        """
        label = await self.extract(image_bytes, beverage_type, mime_type)
        return label, ExtractionAudit(
            fallback_used=False,
            provider_used=self.__class__.__name__,
        )
