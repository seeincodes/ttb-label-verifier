"""Tests for app.verifier.warning — canonical government-warning checks.

Two-layer check per presearch §5.1:
  1. Text content vs canonical 27 CFR 16.21 (case-insensitive,
     whitespace-normalized).
  2. Three formatting yes/no questions per 27 CFR 16.22 (caps, bold,
     continuous).

The orchestrator surfaces the *worst* of the two layers and cites the
section the failure depends on — so a malformed-text label cites 16.21,
a present-but-poorly-formatted label cites 16.22, and an ERROR (low
confidence on the extraction itself) cites neither.
"""
from __future__ import annotations

import pytest

from app.models import WarningFormatting


CANONICAL = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women "
    "should not drink alcoholic beverages during pregnancy because of "
    "the risk of birth defects. (2) Consumption of alcoholic beverages "
    "impairs your ability to drive a car or operate machinery, and may "
    "cause health problems."
)


class TestCanonicalWarningText:
    def test_returns_verbatim_text_from_27_cfr_16_21(self):
        """The canonical text must match the regulation byte-for-byte —
        a transcription typo would force every label into FAIL."""
        from app.verifier.warning import canonical_warning_text

        assert canonical_warning_text() == CANONICAL

    def test_includes_both_numbered_clauses(self):
        from app.verifier.warning import canonical_warning_text

        text = canonical_warning_text()
        assert "(1) According to the Surgeon General" in text
        assert "(2) Consumption of alcoholic beverages" in text


class TestCheckWarningText:
    """Text-layer check, cites 27 CFR 16.21."""

    def test_exact_match_passes(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_text

        fv = check_warning_text(CANONICAL)
        assert fv.verdict is Verdict.PASS

    def test_whitespace_difference_passes(self):
        """Labels often have line-broken warnings — the verifier collapses
        whitespace before comparing so newlines and double-spaces don't
        flag a literal-text FAIL."""
        from app.models import Verdict
        from app.verifier.warning import check_warning_text

        weird_ws = (
            "GOVERNMENT WARNING:   (1) According to the Surgeon General,\n"
            "women should not drink alcoholic beverages during pregnancy\n"
            "because of the risk of birth defects.  (2) Consumption of\n"
            "alcoholic beverages impairs your ability to drive a car or\n"
            "operate machinery, and may cause health problems."
        )
        fv = check_warning_text(weird_ws)
        assert fv.verdict is Verdict.PASS

    def test_case_insensitive(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_text

        fv = check_warning_text(CANONICAL.lower())
        # text-LAYER check is case-insensitive — caps live in the FORMATTING
        # layer (16.22), not the TEXT layer (16.21).
        assert fv.verdict is Verdict.PASS

    def test_missing_clause_fails_with_16_21_cite(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_text

        truncated = (
            "GOVERNMENT WARNING: (1) According to the Surgeon General, "
            "women should not drink alcoholic beverages during pregnancy "
            "because of the risk of birth defects."
        )  # clause (2) missing
        fv = check_warning_text(truncated)
        assert fv.verdict is Verdict.FAIL
        assert "16.21" in fv.cfr_citation
        assert fv.reason  # actionable

    def test_substantive_typo_fails(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_text

        wrong = CANONICAL.replace("Surgeon General", "Surgeon Generalissimo")
        fv = check_warning_text(wrong)
        assert fv.verdict is Verdict.FAIL
        assert "16.21" in fv.cfr_citation

    def test_empty_or_null_returns_error(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_text

        for value in (None, "", "   "):
            fv = check_warning_text(value)
            assert fv.verdict is Verdict.ERROR
            # ERROR isn't a §16.21 violation; it's a per-field confidence /
            # extractability problem. Citation may be empty.


class TestCheckWarningFormatting:
    """Formatting-layer check, cites 27 CFR 16.22."""

    def _wf(self, caps=True, bold=True, continuous=True, confidence="high"):
        return WarningFormatting(
            caps_correct=caps,
            bold_correct=bold,
            continuous=continuous,
            confidence=confidence,
        )

    def test_all_three_pass_passes(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_formatting

        assert check_warning_formatting(self._wf()).verdict is Verdict.PASS

    def test_caps_failure_fails_with_16_22(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_formatting

        fv = check_warning_formatting(self._wf(caps=False))
        assert fv.verdict is Verdict.FAIL
        assert "16.22" in fv.cfr_citation
        assert "caps" in fv.reason.lower() or "capital" in fv.reason.lower()

    def test_bold_failure_fails_with_16_22(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_formatting

        fv = check_warning_formatting(self._wf(bold=False))
        assert fv.verdict is Verdict.FAIL
        assert "16.22" in fv.cfr_citation
        assert "bold" in fv.reason.lower()

    def test_continuous_failure_fails_with_16_22(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_formatting

        fv = check_warning_formatting(self._wf(continuous=False))
        assert fv.verdict is Verdict.FAIL
        assert "16.22" in fv.cfr_citation
        assert "continuous" in fv.reason.lower()

    def test_multiple_failures_listed_in_reason(self):
        from app.models import Verdict
        from app.verifier.warning import check_warning_formatting

        fv = check_warning_formatting(self._wf(caps=False, bold=False))
        assert fv.verdict is Verdict.FAIL
        # both failures should be surfaced so the agent gets a complete fix list
        assert "caps" in fv.reason.lower() or "capital" in fv.reason.lower()
        assert "bold" in fv.reason.lower()

    def test_low_confidence_in_formatting_yields_error(self):
        """If the model can't see the warning well enough to answer the
        three yes/no questions, we cannot assert PASS or FAIL on
        formatting — that's an ERROR, not a regulatory violation."""
        from app.models import Verdict
        from app.verifier.warning import check_warning_formatting

        fv = check_warning_formatting(self._wf(confidence="low"))
        assert fv.verdict is Verdict.ERROR


class TestCheckGovernmentWarningOrchestrator:
    """Combined two-layer check — text layer (16.21) + formatting (16.22)."""

    def _wf(self, caps=True, bold=True, continuous=True, confidence="high"):
        return WarningFormatting(
            caps_correct=caps,
            bold_correct=bold,
            continuous=continuous,
            confidence=confidence,
        )

    def test_both_layers_pass(self):
        from app.models import Verdict
        from app.verifier.warning import check_government_warning

        fv = check_government_warning(CANONICAL, self._wf())
        assert fv.verdict is Verdict.PASS

    def test_text_fail_cites_16_21(self):
        """Text mismatch + formatting OK → FAIL with 16.21 citation."""
        from app.models import Verdict
        from app.verifier.warning import check_government_warning

        bad_text = CANONICAL.replace("birth defects", "developmental issues")
        fv = check_government_warning(bad_text, self._wf())
        assert fv.verdict is Verdict.FAIL
        assert "16.21" in fv.cfr_citation

    def test_formatting_fail_cites_16_22(self):
        """Text OK + formatting violation → FAIL with 16.22 citation."""
        from app.models import Verdict
        from app.verifier.warning import check_government_warning

        fv = check_government_warning(CANONICAL, self._wf(caps=False))
        assert fv.verdict is Verdict.FAIL
        assert "16.22" in fv.cfr_citation

    def test_both_fail_surfaces_both_citations(self):
        """If a label fails both layers, both cites should appear so the
        agent's correction reflects both regulatory bases."""
        from app.models import Verdict
        from app.verifier.warning import check_government_warning

        fv = check_government_warning("nope", self._wf(caps=False))
        assert fv.verdict is Verdict.FAIL
        assert "16.21" in fv.cfr_citation
        assert "16.22" in fv.cfr_citation

    def test_text_error_does_not_mask_pass_formatting(self):
        """If extraction was empty/None, the orchestrator must yield ERROR
        rather than silently passing on the formatting alone — the field
        is unverifiable, not OK."""
        from app.models import Verdict
        from app.verifier.warning import check_government_warning

        fv = check_government_warning(None, self._wf())
        assert fv.verdict is Verdict.ERROR
