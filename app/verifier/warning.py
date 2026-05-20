"""Government-warning verifier — two layers, two CFR sections.

Per presearch §5.1 the warning check has two distinct layers cited to
different sections:

  - **Text content** check (`check_warning_text`) — extract vs canonical
    27 CFR 16.21 verbatim text, case-insensitive after whitespace
    collapse. Cites 16.21 on FAIL.
  - **Formatting** check (`check_warning_formatting`) — three yes/no
    questions on the label image (caps on "GOVERNMENT WARNING", bold
    weight on that phrase, continuous statement). Cites 16.22 on FAIL.

The canonical text lives in exactly one place — this module — so any
update (the warning is updated periodically) lands once. Tests pin the
verbatim string.
"""
from __future__ import annotations

import re

from app.models import FieldVerdict, Verdict, WarningFormatting


# Verbatim canonical warning per 27 CFR 16.21. Keep as one string literal
# (no concatenation) for byte-for-byte reviewability against the regulation.
_CANONICAL_WARNING_TEXT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women "
    "should not drink alcoholic beverages during pregnancy because of "
    "the risk of birth defects. (2) Consumption of alcoholic beverages "
    "impairs your ability to drive a car or operate machinery, and may "
    "cause health problems."
)

_WHITESPACE_RE = re.compile(r"\s+")


def canonical_warning_text() -> str:
    """Return the verbatim canonical text from 27 CFR 16.21."""
    return _CANONICAL_WARNING_TEXT


def _normalize_for_text_compare(s: str) -> str:
    """Whitespace-collapse + lowercase. The text-layer check ignores case
    (caps live in the formatting layer, not the text layer) and ignores
    line breaks / multiple spaces (labels routinely wrap the warning over
    several lines)."""
    return _WHITESPACE_RE.sub(" ", s).strip().lower()


def check_warning_text(extracted: str | None) -> FieldVerdict:
    """Compare extracted warning text to the canonical 27 CFR 16.21 text.

    Args:
        extracted: text returned by the vision model. None / empty signals
            the model couldn't read the warning — that's ERROR, not a
            regulatory violation.

    Returns:
        - PASS if the normalised text matches canonical exactly.
        - FAIL with 27 CFR 16.21 citation if the text is wrong.
        - ERROR if extraction was empty / blank.
    """
    if not extracted or not extracted.strip():
        return FieldVerdict(
            verdict=Verdict.ERROR,
            reason=(
                "government warning text could not be extracted from the label "
                "— please reshoot with the warning fully visible"
            ),
            cfr_citation="",
            comparison_method="canonical_text_exact",
            evidence={"extracted": extracted},
        )

    extracted_norm = _normalize_for_text_compare(extracted)
    canonical_norm = _normalize_for_text_compare(_CANONICAL_WARNING_TEXT)
    if extracted_norm == canonical_norm:
        return FieldVerdict(
            verdict=Verdict.PASS,
            comparison_method="canonical_text_exact",
            evidence={"normalized_match": True},
        )

    return FieldVerdict(
        verdict=Verdict.FAIL,
        reason=(
            "government warning text does not match the canonical text "
            "required by 27 CFR 16.21 (compared after whitespace collapse "
            "and case-fold)"
        ),
        cfr_citation="27 CFR 16.21",
        comparison_method="canonical_text_exact",
        evidence={
            "extracted_normalized": extracted_norm,
            "canonical_normalized": canonical_norm,
        },
    )


def check_warning_formatting(formatting: WarningFormatting) -> FieldVerdict:
    """Check the three-part formatting block (caps, bold, continuous).

    Returns FAIL with 27 CFR 16.22 citation if any of the three is False.
    Returns ERROR if the model's overall confidence in the three answers
    is `low` — we cannot assert PASS or FAIL on formatting we couldn't see.
    """
    if formatting.confidence == "low":
        return FieldVerdict(
            verdict=Verdict.ERROR,
            reason=(
                "vision model could not assess government-warning formatting "
                "with confidence — please reshoot with the warning area sharper"
            ),
            cfr_citation="",
            comparison_method="vision_yes_no_questions",
            evidence={"formatting": formatting.model_dump()},
        )

    failures: list[str] = []
    if not formatting.caps_correct:
        failures.append(
            "the phrase 'GOVERNMENT WARNING' is not rendered in ALL CAPITAL LETTERS"
        )
    if not formatting.bold_correct:
        failures.append(
            "the phrase 'GOVERNMENT WARNING' is not bold (heavier weight than the rest)"
        )
    if not formatting.continuous:
        failures.append(
            "the warning is not a continuous statement (broken up by other content)"
        )

    if not failures:
        return FieldVerdict(
            verdict=Verdict.PASS,
            comparison_method="vision_yes_no_questions",
            evidence={"formatting": formatting.model_dump()},
        )

    return FieldVerdict(
        verdict=Verdict.FAIL,
        reason=(
            "government-warning formatting violates 27 CFR 16.22: "
            + "; ".join(failures)
        ),
        cfr_citation="27 CFR 16.22",
        comparison_method="vision_yes_no_questions",
        evidence={
            "formatting": formatting.model_dump(),
            "violations": failures,
        },
    )


def check_government_warning(
    extracted_text: str | None,
    formatting: WarningFormatting,
) -> FieldVerdict:
    """Run both layers; surface the worst result with the right citation.

    If both layers fail, the citation lists both 16.21 and 16.22 so the
    agent's correction list reflects both regulatory bases. Verdict
    aggregation uses `Verdict.worst_of` so ERROR (extraction couldn't be
    read) dominates a downstream PASS / FAIL on formatting — we never
    assert PASS on a warning we couldn't read.
    """
    text_fv = check_warning_text(extracted_text)
    formatting_fv = check_warning_formatting(formatting)

    overall = Verdict.worst_of([text_fv.verdict, formatting_fv.verdict])

    if overall is Verdict.PASS:
        return FieldVerdict(
            verdict=Verdict.PASS,
            comparison_method="canonical_text + vision_yes_no",
            evidence={
                "text_layer": text_fv.evidence,
                "formatting_layer": formatting_fv.evidence,
            },
        )

    if overall is Verdict.ERROR:
        # Pick whichever layer is ERROR; if both, pass the text-layer
        # message through since the model couldn't extract the warning at
        # all and there's nothing useful to say about formatting.
        primary = text_fv if text_fv.verdict is Verdict.ERROR else formatting_fv
        return FieldVerdict(
            verdict=Verdict.ERROR,
            reason=primary.reason,
            cfr_citation="",
            comparison_method=primary.comparison_method,
            evidence={
                "text_layer": text_fv.evidence,
                "formatting_layer": formatting_fv.evidence,
            },
        )

    # overall is FAIL (or WARN, but the warning rule has no WARN flavor).
    citations: list[str] = []
    reasons: list[str] = []
    if text_fv.verdict is Verdict.FAIL:
        citations.append(text_fv.cfr_citation)
        reasons.append(text_fv.reason)
    if formatting_fv.verdict is Verdict.FAIL:
        citations.append(formatting_fv.cfr_citation)
        reasons.append(formatting_fv.reason)

    return FieldVerdict(
        verdict=Verdict.FAIL,
        reason=" | ".join(reasons),
        cfr_citation=" + ".join(citations),
        comparison_method="canonical_text + vision_yes_no",
        evidence={
            "text_layer": text_fv.evidence,
            "formatting_layer": formatting_fv.evidence,
        },
    )
