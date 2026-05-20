"""Tests for app.extractors.base — the LabelExtractor ABC.

The ABC keeps the verifier provider-agnostic: any concrete extractor returns
the same `LabelData` shape (presearch §5.5), so adding a third provider is a
new file, not a verifier change.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from app.models import (
    BeverageType,
    ExtractedField,
    LabelData,
    WarningFormatting,
)


def _fake_label_data() -> LabelData:
    """Minimal valid LabelData — keeps tests focused on the ABC contract,
    not on the data shape (which test_models.py already covers)."""
    high = "high"
    return LabelData(
        brand_name=ExtractedField[str](value="Acme", confidence=high),
        class_type=ExtractedField[str](value="Bourbon", confidence=high),
        alcohol_content_pct=ExtractedField[float](value=40.0, confidence=high),
        alcohol_content_text=ExtractedField[str](
            value="40% ALC./VOL.", confidence=high
        ),
        net_contents=ExtractedField[str](value="750 mL", confidence=high),
        bottler_name=ExtractedField[str](value="Acme Co", confidence=high),
        bottler_address=ExtractedField[str](
            value="1 Main St", confidence=high
        ),
        country_of_origin=ExtractedField[str](value=None, confidence=high),
        government_warning_text=ExtractedField[str](
            value="GOVERNMENT WARNING: ...", confidence=high
        ),
        government_warning_formatting=WarningFormatting(
            caps_correct=True,
            bold_correct=True,
            continuous=True,
            confidence=high,
        ),
    )


class TestLabelExtractorABC:
    def test_cannot_instantiate_abc_directly(self):
        """The ABC is the abstraction signal in the writeup — instantiating
        it without a concrete subclass should fail, like any ABC."""
        from app.extractors.base import LabelExtractor

        with pytest.raises(TypeError):
            LabelExtractor()  # type: ignore[abstract]

    def test_extract_is_an_abstract_method(self):
        from app.extractors.base import LabelExtractor

        assert "extract" in LabelExtractor.__abstractmethods__

    def test_extract_signature_matches_spec(self):
        """Per task list: extract(image_bytes: bytes, beverage_type: BeverageType)
        -> LabelData. Plus a mime_type param so the SDK call gets the right
        Content-Type header (presearch ERROR_FIX_LOG: "missing MIME often
        manifests as a vague 400")."""
        from app.extractors.base import LabelExtractor

        sig = inspect.signature(LabelExtractor.extract)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert "self" in names
        assert "image_bytes" in names
        assert "beverage_type" in names
        assert "mime_type" in names

    def test_concrete_subclass_can_be_instantiated_and_called(self):
        """The contract: a subclass implements `extract` and returns
        LabelData; callers don't care which provider produced it."""
        from app.extractors.base import LabelExtractor

        class StubExtractor(LabelExtractor):
            async def extract(
                self,
                image_bytes: bytes,
                beverage_type: BeverageType,
                mime_type: str = "image/jpeg",
            ) -> LabelData:
                return _fake_label_data()

        stub = StubExtractor()
        result = asyncio.run(
            stub.extract(b"\x00", BeverageType.DISTILLED_SPIRITS)
        )
        assert isinstance(result, LabelData)
        assert result.brand_name.value == "Acme"

    def test_extract_is_async(self):
        """Async path is non-negotiable — FastAPI is async and the batch
        flow runs N extractions concurrently under asyncio.Semaphore."""
        from app.extractors.base import LabelExtractor

        assert inspect.iscoroutinefunction(LabelExtractor.extract)
