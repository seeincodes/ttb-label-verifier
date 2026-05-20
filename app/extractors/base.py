"""LabelExtractor ABC — the single abstraction the verifier sees.

Every concrete provider (Gemini, OpenAI, future Claude / Bedrock) implements
`extract` and returns the same `LabelData` shape (presearch §5.5). The
verifier therefore never reaches into provider-specific JSON; switching
providers is a one-line factory change.

The ABC is intentionally tiny (~30 lines). The writeup §10 calls out
rolling this rather than depending on LiteLLM / LangChain as deliberate
architectural signal — keep it that way.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import BeverageType, LabelData


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
