"""Tests for app.verifier.normalize.

Normalization is the layer beneath fuzzy matching and tolerance checks.
Each helper is intentionally narrow so rules compose them — `normalize_text`
for fuzzy-match prep, `normalize_volume` + `volumes_equivalent` for net
contents, `strip_corporate_suffixes` for bottler comparisons.
"""
from __future__ import annotations

import pytest


class TestNormalizeText:
    """Lowercase, strip punctuation, collapse whitespace. Used as the input
    to rapidfuzz token_sort_ratio so cosmetic-only differences score 100."""

    def test_lowercases(self):
        from app.verifier.normalize import normalize_text

        assert normalize_text("OLD TOM DISTILLERY") == "old tom distillery"

    def test_strips_punctuation(self):
        from app.verifier.normalize import normalize_text

        # Per presearch §5.4 "STONE'S THROW" vs "Stone's Throw" must score
        # 100 — apostrophe is irrelevant noise for brand comparison.
        a = normalize_text("STONE'S THROW")
        b = normalize_text("Stone's Throw")
        assert a == b

    def test_collapses_whitespace(self):
        from app.verifier.normalize import normalize_text

        assert (
            normalize_text("  Stone's   Throw\tDistillery  ")
            == "stones throw distillery"
        )

    def test_unicode_punctuation_handled(self):
        from app.verifier.normalize import normalize_text

        # Curly apostrophe + em-dash — labels regularly print these.
        assert normalize_text("Stone’s—Throw") == "stones throw"

    def test_empty_string_returns_empty(self):
        from app.verifier.normalize import normalize_text

        assert normalize_text("") == ""
        assert normalize_text("   ") == ""

    def test_keeps_digits(self):
        """A '12 Year' designation must not lose its digits."""
        from app.verifier.normalize import normalize_text

        assert normalize_text("12 Year Bourbon!") == "12 year bourbon"


class TestNormalizeVolume:
    """Parse "750 mL", "0.75 L", "1.5 L", "12 FL OZ" → milliliters."""

    @pytest.mark.parametrize(
        "raw,expected_ml",
        [
            ("750 mL", 750.0),
            ("750ml", 750.0),
            ("750 ML", 750.0),
            ("0.75 L", 750.0),
            ("0.75l", 750.0),
            ("1.5 L", 1500.0),
            ("1 L", 1000.0),
            ("355 mL", 355.0),
            # US fl oz — 750 ml ≈ 25.36 fl oz on TTB labels.
            ("12 fl oz", pytest.approx(354.882, rel=1e-3)),
            ("12 FL. OZ.", pytest.approx(354.882, rel=1e-3)),
        ],
    )
    def test_parses_common_label_volumes(self, raw, expected_ml):
        from app.verifier.normalize import normalize_volume

        assert normalize_volume(raw) == expected_ml

    def test_returns_none_for_unparseable(self):
        from app.verifier.normalize import normalize_volume

        assert normalize_volume("bottle") is None
        assert normalize_volume("") is None
        assert normalize_volume("abc mL") is None

    def test_handles_extra_whitespace(self):
        from app.verifier.normalize import normalize_volume

        assert normalize_volume("  750   mL  ") == 750.0


class TestVolumesEquivalent:
    """Semantic equivalence between two label volume strings — used by the
    net-contents rule so '750 mL' and '0.75 L' silently PASS (§5.4)."""

    def test_identical_strings_equivalent(self):
        from app.verifier.normalize import volumes_equivalent

        assert volumes_equivalent("750 mL", "750 mL") is True

    def test_unit_variants_equivalent(self):
        from app.verifier.normalize import volumes_equivalent

        assert volumes_equivalent("750 mL", "0.75 L") is True
        assert volumes_equivalent("1.5 L", "1500 mL") is True

    def test_different_volumes_not_equivalent(self):
        from app.verifier.normalize import volumes_equivalent

        assert volumes_equivalent("750 mL", "700 mL") is False
        assert volumes_equivalent("750 mL", "1 L") is False

    def test_within_default_tolerance(self):
        """A 0.5% tolerance covers rounding on the label (TTB rounds to
        whole mL on volumes ≤ 1 L). 750 vs 748 should still PASS."""
        from app.verifier.normalize import volumes_equivalent

        assert volumes_equivalent("750 mL", "748 mL") is True
        # but 5% drift is a real different volume.
        assert volumes_equivalent("750 mL", "712 mL") is False

    def test_unparseable_input_is_not_equivalent(self):
        """If either side won't parse, callers should know — return False so
        the rule layer can FAIL with an actionable reason rather than
        silently passing on garbage."""
        from app.verifier.normalize import volumes_equivalent

        assert volumes_equivalent("bottle", "750 mL") is False
        assert volumes_equivalent("750 mL", "") is False


class TestStripCorporateSuffixes:
    """Drop trailing entity suffixes for bottler-name comparison so
    'Old Tom Distillery LLC' vs 'Old Tom Distillery' aligns (§5.4)."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Old Tom Distillery LLC", "Old Tom Distillery"),
            ("Old Tom Distillery, LLC", "Old Tom Distillery"),
            ("Old Tom Distillery, L.L.C.", "Old Tom Distillery"),
            ("Old Tom Distillery Inc", "Old Tom Distillery"),
            ("Old Tom Distillery, Inc.", "Old Tom Distillery"),
            ("Old Tom Distillery Co.", "Old Tom Distillery"),
            ("Old Tom Distillery Corp.", "Old Tom Distillery"),
            ("Old Tom Distillery Ltd.", "Old Tom Distillery"),
            ("Old Tom Distillery Company", "Old Tom Distillery"),
            ("Old Tom Distillery Corporation", "Old Tom Distillery"),
        ],
    )
    def test_strips_common_suffix(self, raw, expected):
        from app.verifier.normalize import strip_corporate_suffixes

        assert strip_corporate_suffixes(raw) == expected

    def test_no_suffix_returned_unchanged(self):
        from app.verifier.normalize import strip_corporate_suffixes

        assert strip_corporate_suffixes("Old Tom Distillery") == "Old Tom Distillery"

    def test_only_trailing_suffix_stripped(self):
        """'Company Distillers Inc' should keep 'Company' in the middle."""
        from app.verifier.normalize import strip_corporate_suffixes

        assert (
            strip_corporate_suffixes("Company Distillers Inc")
            == "Company Distillers"
        )

    def test_case_insensitive(self):
        from app.verifier.normalize import strip_corporate_suffixes

        assert strip_corporate_suffixes("OLD TOM DISTILLERY llc") == "OLD TOM DISTILLERY"

    def test_strips_then_text_normalize_chains(self):
        """The rules layer will chain: strip → normalize_text → fuzzy."""
        from app.verifier.normalize import (
            normalize_text,
            strip_corporate_suffixes,
        )

        a = normalize_text(strip_corporate_suffixes("Old Tom Distillery LLC"))
        b = normalize_text(strip_corporate_suffixes("Old Tom Distillery"))
        assert a == b
