"""Tests for the extractor factory + FallbackExtractor.

Factory: `app.extractors.build_extractor()` selects on `EXTRACTOR_PROVIDER`
(gemini | openai) and wraps the result in a FallbackExtractor that retries
once on a transient ExtractorError.

The whole thing is tested without network — production wiring uses the
real Gemini / OpenAI clients, but the factory's selection logic and the
fallback semantics are pinned by stub-based tests.
"""
from __future__ import annotations

import asyncio

import pytest

from app.extractors.base import LabelExtractor
from app.extractors.gemini import ExtractorError
from app.models import BeverageType, ExtractedField, LabelData, WarningFormatting


def _canonical_warning_text():
    from app.verifier.warning import canonical_warning_text

    return canonical_warning_text()


def _fake_label(brand="Acme") -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value=brand, confidence="high"),
        class_type=ExtractedField(value="Bourbon", confidence="high"),
        alcohol_content_pct=ExtractedField(value=45.0, confidence="high"),
        alcohol_content_text=ExtractedField(value="45% ALC./VOL.", confidence="high"),
        net_contents=ExtractedField(value="750 mL", confidence="high"),
        bottler_name=ExtractedField(value=brand, confidence="high"),
        bottler_address=ExtractedField(value="1 Main", confidence="high"),
        country_of_origin=ExtractedField(value=None, confidence="high"),
        government_warning_text=ExtractedField(
            value=_canonical_warning_text(), confidence="high"
        ),
        government_warning_formatting=WarningFormatting(
            caps_correct=True, bold_correct=True, continuous=True, confidence="high"
        ),
    )


class StubPrimary(LabelExtractor):
    def __init__(self, label: LabelData | None = None, raise_with: Exception | None = None):
        self.label = label or _fake_label("PRIMARY")
        self.raise_with = raise_with
        self.calls = 0

    async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
        self.calls += 1
        if self.raise_with is not None:
            raise self.raise_with
        return self.label


class StubSecondary(LabelExtractor):
    def __init__(self, label: LabelData | None = None, raise_with: Exception | None = None):
        self.label = label or _fake_label("SECONDARY")
        self.raise_with = raise_with
        self.calls = 0

    async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
        self.calls += 1
        if self.raise_with is not None:
            raise self.raise_with
        return self.label


class TestFallbackExtractor:
    def test_primary_succeeds_no_fallback(self):
        from app.extractors import FallbackExtractor

        primary = StubPrimary()
        secondary = StubSecondary()
        fe = FallbackExtractor(primary=primary, secondary=secondary)

        label, audit = asyncio.run(
            fe.extract_with_audit(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
        )
        assert label.brand_name.value == "PRIMARY"
        assert audit.fallback_used is False
        assert primary.calls == 1
        assert secondary.calls == 0

    def test_primary_fails_secondary_succeeds(self):
        from app.extractors import FallbackExtractor

        primary = StubPrimary(raise_with=ExtractorError("Gemini 503 UNAVAILABLE"))
        secondary = StubSecondary()
        fe = FallbackExtractor(primary=primary, secondary=secondary)

        label, audit = asyncio.run(
            fe.extract_with_audit(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
        )
        assert label.brand_name.value == "SECONDARY"
        assert audit.fallback_used is True
        assert primary.calls == 1
        assert secondary.calls == 1

    def test_both_fail_raises(self):
        """If both providers fail, the wrapper raises so the route surfaces
        the error fragment. The user shouldn't see a silent dummy verdict."""
        from app.extractors import FallbackExtractor

        primary = StubPrimary(raise_with=ExtractorError("Gemini 503"))
        secondary = StubSecondary(raise_with=ExtractorError("OpenAI 503"))
        fe = FallbackExtractor(primary=primary, secondary=secondary)

        with pytest.raises(ExtractorError) as exc_info:
            asyncio.run(
                fe.extract_with_audit(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
            )
        # Message should mention both providers so the audit log captures
        # what fell back to what.
        msg = str(exc_info.value).lower()
        assert "primary" in msg or "openai" in msg
        assert "secondary" in msg or "gemini" in msg or "503" in msg

    def test_secondary_not_called_unnecessarily(self):
        """Verify the wrapper doesn't pre-emptively warm up the secondary —
        OpenAI calls are billed and Gemini is healthy most of the time."""
        from app.extractors import FallbackExtractor

        primary = StubPrimary()
        secondary = StubSecondary()
        fe = FallbackExtractor(primary=primary, secondary=secondary)

        for _ in range(5):
            asyncio.run(
                fe.extract_with_audit(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
            )
        assert primary.calls == 5
        assert secondary.calls == 0

    def test_extract_method_also_works(self):
        """The bare `extract()` method should still work — same semantics,
        loses only the audit metadata."""
        from app.extractors import FallbackExtractor

        primary = StubPrimary(raise_with=ExtractorError("503"))
        secondary = StubSecondary()
        fe = FallbackExtractor(primary=primary, secondary=secondary)

        label = asyncio.run(
            fe.extract(b"x", BeverageType.DISTILLED_SPIRITS, "image/png")
        )
        assert label.brand_name.value == "SECONDARY"


class TestBuildExtractor:
    """Factory selects the primary on EXTRACTOR_PROVIDER. The result is
    always wrapped in FallbackExtractor; primary = the configured provider,
    secondary = the other one."""

    def test_gemini_primary_openai_secondary(self, monkeypatch):
        from app.extractors import build_extractor

        # Need keys present so the factories don't bail out.
        monkeypatch.setenv("EXTRACTOR_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", "fake-but-non-empty")
        monkeypatch.setenv("OPENAI_API_KEY", "fake-but-non-empty")

        from app.config import get_settings

        get_settings.cache_clear()

        ex = build_extractor()
        # Wrapped in FallbackExtractor
        from app.extractors import FallbackExtractor
        from app.extractors.gemini import GeminiExtractor
        from app.extractors.openai import OpenAIExtractor

        assert isinstance(ex, FallbackExtractor)
        assert isinstance(ex.primary, GeminiExtractor)
        assert isinstance(ex.secondary, OpenAIExtractor)

        get_settings.cache_clear()

    def test_openai_primary_gemini_secondary(self, monkeypatch):
        from app.extractors import build_extractor

        monkeypatch.setenv("EXTRACTOR_PROVIDER", "openai")
        monkeypatch.setenv("GEMINI_API_KEY", "fake-but-non-empty")
        monkeypatch.setenv("OPENAI_API_KEY", "fake-but-non-empty")

        from app.config import get_settings

        get_settings.cache_clear()

        from app.extractors import FallbackExtractor, build_extractor
        from app.extractors.gemini import GeminiExtractor
        from app.extractors.openai import OpenAIExtractor

        ex = build_extractor()
        assert isinstance(ex, FallbackExtractor)
        assert isinstance(ex.primary, OpenAIExtractor)
        assert isinstance(ex.secondary, GeminiExtractor)

        get_settings.cache_clear()

    def test_only_secondary_key_missing_still_builds_solo(self, monkeypatch):
        """If only one key is set, the factory should build a single-provider
        extractor (no fallback wrapper, or a wrapper where secondary is None
        and is_a_no_op). A missing fallback key shouldn't break the primary."""
        from app.config import get_settings

        monkeypatch.setenv("EXTRACTOR_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        get_settings.cache_clear()

        from app.extractors import build_extractor
        from app.extractors.gemini import GeminiExtractor

        ex = build_extractor()
        # Either a bare GeminiExtractor or a FallbackExtractor whose secondary is None.
        from app.extractors import FallbackExtractor

        if isinstance(ex, FallbackExtractor):
            assert isinstance(ex.primary, GeminiExtractor)
            assert ex.secondary is None
        else:
            assert isinstance(ex, GeminiExtractor)

        get_settings.cache_clear()
