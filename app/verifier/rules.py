"""Per-field verifier rules + the `verify_label` orchestrator.

One function per checklist field. Each rule:
  - has a docstring citing the relevant 27 CFR section,
  - returns a `FieldVerdict` with `verdict`, `reason`, `cfr_citation`,
    `comparison_method`, `evidence`,
  - applies the per-field confidence gate: any required field at
    `low` confidence becomes ERROR rather than risk a false PASS / FAIL
    (MVP9).

The orchestrator `verify_label` walks the §5.6 conditionality matrix and
returns a dict of {field_name: FieldVerdict}. Fields not required for the
given beverage type are silently skipped, never failed.

Citations used in this module:
  - 27 CFR 5.32  — spirits brand-name standards
  - 27 CFR 5.35  — spirits class / type
  - 27 CFR 5.38  — spirits net contents
  - 27 CFR 5.65  — spirits ABV (statement of composition / tolerance)
  - 27 CFR 5.36  — spirits country of origin
  - 27 CFR 4.32  — wine brand name
  - 27 CFR 4.21  — wine class / type
  - 27 CFR 4.36  — wine ABV
  - 27 CFR 4.37  — wine net contents
  - 27 CFR 4.39  — wine country of origin
  - 27 CFR 7.22  — malt brand name
  - 27 CFR 7.27  — malt net contents
  - 27 CFR 7.65  — malt ABV
  - 27 CFR 7.26  — malt country of origin
  - 27 CFR 16.21 / 16.22 — government warning (in app/verifier/warning.py)
"""
from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz

from app.models import (
    ApplicationData,
    BeverageType,
    ExtractedField,
    FieldVerdict,
    LabelData,
    Verdict,
    WarningFormatting,
)
from app.verifier.normalize import (
    normalize_text,
    normalize_volume,
    strip_corporate_suffixes,
    volumes_equivalent,
)
from app.verifier.tolerances import tolerance_for
from app.verifier.warning import check_government_warning


# ---------------------------------------------------------------------------
# Per-beverage CFR citation lookups
# ---------------------------------------------------------------------------

_BRAND_CITATIONS = {
    BeverageType.DISTILLED_SPIRITS: "27 CFR 5.32",
    BeverageType.WINE: "27 CFR 4.32",
    BeverageType.MALT_BEVERAGE: "27 CFR 7.22",
    BeverageType.OTHER: "27 CFR 5.32 (by analogy)",
}

_CLASS_TYPE_CITATIONS = {
    BeverageType.DISTILLED_SPIRITS: "27 CFR 5.35",
    BeverageType.WINE: "27 CFR 4.21",
    BeverageType.MALT_BEVERAGE: "27 CFR 7.22",
    BeverageType.OTHER: "27 CFR 5.35 (by analogy)",
}

_NET_CONTENTS_CITATIONS = {
    BeverageType.DISTILLED_SPIRITS: "27 CFR 5.38",
    BeverageType.WINE: "27 CFR 4.37",
    BeverageType.MALT_BEVERAGE: "27 CFR 7.27",
    BeverageType.OTHER: "27 CFR 5.38 (by analogy)",
}

_BOTTLER_CITATIONS = {
    BeverageType.DISTILLED_SPIRITS: "27 CFR 5.36",
    BeverageType.WINE: "27 CFR 4.35",
    BeverageType.MALT_BEVERAGE: "27 CFR 7.25",
    BeverageType.OTHER: "27 CFR 5.36 (by analogy)",
}

_COUNTRY_CITATIONS = {
    BeverageType.DISTILLED_SPIRITS: "27 CFR 5.36(d)",
    BeverageType.WINE: "27 CFR 4.39",
    BeverageType.MALT_BEVERAGE: "27 CFR 7.26",
    BeverageType.OTHER: "27 CFR 5.36(d) (by analogy)",
}


# ---------------------------------------------------------------------------
# Fuzzy-match thresholds (presearch §5.4)
# ---------------------------------------------------------------------------

# `token_sort_ratio` returns 0–100. ≥95 = silent PASS (cosmetic difference),
# 80–94 = WARN (borderline, agent reviews), <80 = FAIL.
_FUZZY_PASS_THRESHOLD = 95
_FUZZY_WARN_THRESHOLD = 80


def _fuzzy_score(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(normalize_text(a), normalize_text(b))


# ---------------------------------------------------------------------------
# Confidence-gate helper (MVP9)
# ---------------------------------------------------------------------------


def _confidence_error(
    field_label: str,
    method: str,
    extracted: ExtractedField,
) -> FieldVerdict:
    """Build the canonical 'low confidence on required field' ERROR verdict.

    Used by every required-field rule (MVP9 §5.1). The reason mentions the
    field name and instructs a reshoot — §5.2's actionable-error contract.
    """
    return FieldVerdict(
        verdict=Verdict.ERROR,
        reason=(
            f"{field_label} could not be extracted with confidence — "
            f"please reshoot the label so {field_label} is clearly visible"
        ),
        cfr_citation="",
        comparison_method=method,
        evidence={
            "extracted_value": extracted.value,
            "extracted_confidence": extracted.confidence,
        },
    )


def _optional_unverifiable_verdict(
    *,
    field_label: str,
    method: str,
    extracted: ExtractedField,
    citation: str,
) -> FieldVerdict:
    """MVP9 §5.3: optional × low-confidence → WARN 'unverifiable', not ERROR.

    Surfaces in the per-field verdict table so the agent sees we didn't
    check this field, but doesn't bubble to overall ERROR (which would
    force a reshoot for a field the regulator doesn't require for this
    beverage type). The citation is included so the audit trail records
    *which* optional regulation we declined to enforce.
    """
    return FieldVerdict(
        verdict=Verdict.WARN,
        reason=(
            f"{field_label} extracted at low confidence — unverifiable from "
            f"this image; the field is optional for this beverage type so "
            f"the overall verdict is not blocked, but a human should glance "
            f"at the label to confirm ({citation})"
        ),
        cfr_citation=citation,
        comparison_method=method,
        evidence={
            "extracted_value": extracted.value,
            "extracted_confidence": extracted.confidence,
            "gate": "optional_unverifiable",
        },
    )


# ---------------------------------------------------------------------------
# Field rules
# ---------------------------------------------------------------------------


def check_brand_name(
    extracted: ExtractedField[str],
    expected: str,
    beverage: BeverageType = BeverageType.DISTILLED_SPIRITS,
) -> FieldVerdict:
    """Verify the brand name on the label matches the application.

    Cosmetic differences (case, punctuation) silently PASS via the
    normalize-then-fuzzy-≥95 path (presearch §5.4 STONE'S THROW example).
    Borderline 80–94 → WARN. <80 → FAIL.

    Cite: 27 CFR 5.32 (spirits), 4.32 (wine), 7.22 (malt).
    """
    citation = _BRAND_CITATIONS[beverage]
    if extracted.confidence == "low" or extracted.value is None:
        return _confidence_error("brand name", "fuzzy_token_sort", extracted)

    score = _fuzzy_score(extracted.value, expected)
    return _fuzzy_verdict(
        extracted_value=extracted.value,
        expected=expected,
        score=score,
        field_label="brand name",
        citation=citation,
    )


def check_class_type(
    extracted: ExtractedField[str],
    expected: Optional[str],
    beverage: BeverageType,
) -> FieldVerdict:
    """Verify class / type designation.

    Cite: 27 CFR 5.35 (spirits), 4.21 (wine), 7.22 (malt).
    """
    citation = _CLASS_TYPE_CITATIONS[beverage]
    if expected is None:
        # Caller is asking us to check a field they didn't provide an
        # expected value for. Treat as PASS — nothing to compare against.
        return FieldVerdict(
            verdict=Verdict.PASS,
            comparison_method="fuzzy_token_sort",
            evidence={"reason": "no expected class/type provided"},
        )
    if extracted.confidence == "low" or extracted.value is None:
        return _confidence_error("class/type", "fuzzy_token_sort", extracted)

    score = _fuzzy_score(extracted.value, expected)
    return _fuzzy_verdict(
        extracted_value=extracted.value,
        expected=expected,
        score=score,
        field_label="class/type",
        citation=citation,
    )


# Regex matching the prohibited literal "ABV" — case-insensitive standalone
# token. We allow it only as part of acceptable forms like "ALC. BY VOL." —
# the prohibition is on the abbreviation "ABV" itself.
_ABV_ABBREVIATION_RE = re.compile(r"\bABV\b", re.IGNORECASE)


def check_alcohol_content(
    extracted_pct: ExtractedField[float],
    extracted_text: ExtractedField[str],
    expected_pct: float,
    beverage: BeverageType,
) -> FieldVerdict:
    """Verify alcohol content — numeric tolerance AND abbreviation form.

    Two regulatory checks in one rule (because both fail-modes cite the
    same section):

      1. Numeric tolerance per `tolerance_for(beverage, expected_pct)`:
         ≤ tol → PASS, ≤ 2× tol → WARN, > 2× tol → FAIL.
      2. Forbidden 'ABV' abbreviation on the label text: any occurrence of
         the literal substring "ABV" (case-insensitive) → FAIL.
         Acceptable: "Alc. by Vol.", "Alc./Vol.", "ALC. BY VOL.", with or
         without periods, with or without %.

    Cite: 27 CFR 5.65(b) (spirits), 7.65(c) (malt), 4.36 (wine).
    """
    tol = tolerance_for(beverage, expected_pct)

    # Confidence gate on the numeric ABV (the text version is allowed to be
    # lower-confidence since the ABV-abbreviation check tolerates that).
    if extracted_pct.confidence == "low" or extracted_pct.value is None:
        return _confidence_error(
            "alcohol content", "numeric_tolerance", extracted_pct
        )

    # ABV-abbreviation check — runs first because it's a flat regulatory
    # FAIL regardless of the numeric value.
    if extracted_text.value and _ABV_ABBREVIATION_RE.search(extracted_text.value):
        return FieldVerdict(
            verdict=Verdict.FAIL,
            reason=(
                f"label uses prohibited abbreviation 'ABV' "
                f"({extracted_text.value!r}); acceptable forms are "
                f"'Alc. by Vol.', 'Alc./Vol.', 'ALC. BY VOL.', per {tol.cfr_citation}"
            ),
            cfr_citation=tol.cfr_citation,
            comparison_method="abv_abbreviation_check",
            evidence={
                "extracted_text": extracted_text.value,
                "extracted_pct": extracted_pct.value,
            },
        )

    delta = abs(extracted_pct.value - expected_pct)
    if delta <= tol.pp:
        return FieldVerdict(
            verdict=Verdict.PASS,
            comparison_method="numeric_tolerance",
            evidence={
                "extracted_pct": extracted_pct.value,
                "expected_pct": expected_pct,
                "delta_pp": round(delta, 4),
                "tolerance_pp": tol.pp,
                "cfr": tol.cfr_citation,
            },
        )

    if delta <= 2 * tol.pp:
        verdict = Verdict.WARN
    else:
        verdict = Verdict.FAIL

    return FieldVerdict(
        verdict=verdict,
        reason=(
            f"alcohol content {extracted_pct.value}% differs from expected "
            f"{expected_pct}% by {delta:.2f}pp "
            f"(tolerance ±{tol.pp}pp per {tol.cfr_citation})"
        ),
        cfr_citation=tol.cfr_citation,
        comparison_method="numeric_tolerance",
        evidence={
            "extracted_pct": extracted_pct.value,
            "expected_pct": expected_pct,
            "delta_pp": round(delta, 4),
            "tolerance_pp": tol.pp,
        },
    )


def check_net_contents(
    extracted: ExtractedField[str],
    expected: str,
    beverage: BeverageType,
) -> FieldVerdict:
    """Verify net contents (volume) on the label.

    Equivalent representations (750 mL ↔ 0.75 L) silently PASS via
    `volumes_equivalent`. Unparseable extracted value → FAIL with an
    actionable reason rather than silently passing.

    Cite: 27 CFR 5.38 (spirits), 4.37 (wine), 7.27 (malt).
    """
    citation = _NET_CONTENTS_CITATIONS[beverage]
    if extracted.confidence == "low" or extracted.value is None:
        return _confidence_error("net contents", "volume_normalize", extracted)

    if volumes_equivalent(extracted.value, expected):
        return FieldVerdict(
            verdict=Verdict.PASS,
            comparison_method="volume_normalize",
            evidence={"extracted": extracted.value, "expected": expected},
        )

    ml_extracted = normalize_volume(extracted.value)
    if ml_extracted is None:
        return FieldVerdict(
            verdict=Verdict.FAIL,
            reason=(
                f"net contents '{extracted.value}' could not be parsed as a volume "
                f"— expected a value like '750 mL' or '0.75 L' per {citation}"
            ),
            cfr_citation=citation,
            comparison_method="volume_normalize",
            evidence={"extracted": extracted.value, "expected": expected},
        )

    return FieldVerdict(
        verdict=Verdict.FAIL,
        reason=(
            f"net contents '{extracted.value}' does not match expected "
            f"'{expected}' (per {citation})"
        ),
        cfr_citation=citation,
        comparison_method="volume_normalize",
        evidence={"extracted": extracted.value, "expected": expected},
    )


def check_bottler_name(
    extracted: ExtractedField[str],
    expected: str,
    beverage: BeverageType = BeverageType.DISTILLED_SPIRITS,
) -> FieldVerdict:
    """Verify bottler / producer name.

    Corporate-suffix differences ('LLC' vs no suffix) silently PASS via
    `strip_corporate_suffixes` (presearch §5.4 borderline-match path).

    Cite: 27 CFR 5.36 (spirits), 4.35 (wine), 7.25 (malt).
    """
    citation = _BOTTLER_CITATIONS[beverage]
    if extracted.confidence == "low" or extracted.value is None:
        return _confidence_error("bottler name", "fuzzy_token_sort", extracted)

    a = strip_corporate_suffixes(extracted.value)
    b = strip_corporate_suffixes(expected)
    score = _fuzzy_score(a, b)
    return _fuzzy_verdict(
        extracted_value=extracted.value,
        expected=expected,
        score=score,
        field_label="bottler name",
        citation=citation,
    )


def check_bottler_address(
    extracted: ExtractedField[str],
    expected: str,
    beverage: BeverageType = BeverageType.DISTILLED_SPIRITS,
) -> FieldVerdict:
    """Verify bottler address. Treated as a fuzzy text match — addresses
    on labels are often abbreviated ('Frankfort, KY' vs 'Frankfort, Kentucky')
    so the same 95/80/below thresholds apply.

    Cite: 27 CFR 5.36 (spirits), 4.35 (wine), 7.25 (malt).
    """
    citation = _BOTTLER_CITATIONS[beverage]
    if extracted.confidence == "low" or extracted.value is None:
        return _confidence_error("bottler address", "fuzzy_token_sort", extracted)

    score = _fuzzy_score(extracted.value, expected)
    return _fuzzy_verdict(
        extracted_value=extracted.value,
        expected=expected,
        score=score,
        field_label="bottler address",
        citation=citation,
    )


def check_country_of_origin(
    extracted: ExtractedField[Optional[str]],
    expected: Optional[str],
    is_import: bool,
    beverage: BeverageType = BeverageType.DISTILLED_SPIRITS,
) -> FieldVerdict:
    """Verify country of origin — required iff `is_import=True`.

    Cite: 27 CFR 5.36(d) (spirits), 4.39 (wine), 7.26 (malt).
    """
    citation = _COUNTRY_CITATIONS[beverage]

    if not is_import:
        return FieldVerdict(
            verdict=Verdict.PASS,
            comparison_method="conditional_skip",
            evidence={"reason": "domestic label, country_of_origin not required"},
        )

    if extracted.confidence == "low":
        return _confidence_error("country of origin", "fuzzy_token_sort", extracted)

    if extracted.value is None:
        return FieldVerdict(
            verdict=Verdict.FAIL,
            reason=(
                f"country of origin is required for import labels "
                f"(expected {expected!r}) but is not visible on the label "
                f"— per {citation}"
            ),
            cfr_citation=citation,
            comparison_method="fuzzy_token_sort",
            evidence={"extracted": None, "expected": expected, "is_import": True},
        )

    score = _fuzzy_score(extracted.value, expected or "")
    return _fuzzy_verdict(
        extracted_value=extracted.value,
        expected=expected or "",
        score=score,
        field_label="country of origin",
        citation=citation,
    )


# ---------------------------------------------------------------------------
# Shared fuzzy → verdict translator
# ---------------------------------------------------------------------------


def _fuzzy_verdict(
    *,
    extracted_value: str,
    expected: str,
    score: float,
    field_label: str,
    citation: str,
) -> FieldVerdict:
    """Translate a 0–100 fuzzy score into PASS / WARN / FAIL.

    Threshold rationale (presearch §5.4): ≥95 is cosmetic-only, 80–94 is
    "looks similar enough that a human should glance at it", < 80 is a
    different value. Tune against the eval suite, not against vibes.
    """
    evidence = {
        "extracted": extracted_value,
        "expected": expected,
        "fuzzy_score": round(score, 2),
    }
    if score >= _FUZZY_PASS_THRESHOLD:
        return FieldVerdict(
            verdict=Verdict.PASS,
            comparison_method="fuzzy_token_sort",
            evidence=evidence,
        )
    if score >= _FUZZY_WARN_THRESHOLD:
        return FieldVerdict(
            verdict=Verdict.WARN,
            reason=(
                f"{field_label} '{extracted_value}' is similar to expected "
                f"'{expected}' but not identical (fuzzy score {score:.1f}/100) — "
                f"human review recommended per {citation}"
            ),
            cfr_citation=citation,
            comparison_method="fuzzy_token_sort",
            evidence=evidence,
        )
    return FieldVerdict(
        verdict=Verdict.FAIL,
        reason=(
            f"{field_label} '{extracted_value}' does not match expected "
            f"'{expected}' (fuzzy score {score:.1f}/100, < 80) — per {citation}"
        ),
        cfr_citation=citation,
        comparison_method="fuzzy_token_sort",
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# §5.6 conditionality matrix
# ---------------------------------------------------------------------------


# True = field is required for this beverage type (rule runs and ERRORs on
# low confidence). False = optional (rule may run but a missing field is
# treated as PASS, never ERROR).
_CLASS_TYPE_REQUIRED = {
    BeverageType.DISTILLED_SPIRITS: True,
    BeverageType.WINE: True,
    BeverageType.MALT_BEVERAGE: False,
    BeverageType.OTHER: False,
}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def verify_label(
    label: LabelData, application: ApplicationData
) -> dict[str, FieldVerdict]:
    """Run every applicable rule for the given (label, application) pair.

    Returns a dict keyed by field name. `Verdict.worst_of(values)` gives
    the overall verdict — `VerificationResult.model_validator` enforces
    that derivation.

    Beverage-type conditionality (presearch §5.6) is honored here:
      - class/type runs always for spirits & wine; for malt & other, an
        absent expected value or low-confidence extraction returns PASS
        rather than ERROR.
      - country of origin runs only when `is_import=True`.
      - government warning runs for every beverage type.

    Bottler address is verified only if the application supplies it.
    """
    beverage = application.beverage_type
    verdicts: dict[str, FieldVerdict] = {}

    # 1. Brand name (always required).
    verdicts["brand_name"] = check_brand_name(
        label.brand_name, application.brand_name, beverage
    )

    # 2. Class / type — conditional on beverage type.
    if _CLASS_TYPE_REQUIRED[beverage]:
        verdicts["class_type"] = check_class_type(
            label.class_type, application.class_type, beverage
        )
    elif application.class_type is not None:
        # Optional for this beverage type but the agent supplied an
        # expected value. Per MVP9 §5.3: if extraction confidence is low,
        # surface a WARN ("unverifiable") so the agent sees that we didn't
        # check this field — but never ERROR (would force a reshoot for a
        # field the regulator doesn't require for this beverage type).
        if label.class_type.confidence == "low":
            verdicts["class_type"] = _optional_unverifiable_verdict(
                field_label="class/type",
                method="fuzzy_token_sort",
                extracted=label.class_type,
                citation=_CLASS_TYPE_CITATIONS[beverage],
            )
        else:
            verdicts["class_type"] = check_class_type(
                label.class_type, application.class_type, beverage
            )
    # else: application gave us no expected value AND beverage doesn't
    # require it — silently skip per §5.6.

    # 3. Alcohol content (required for spirits + OTHER; conditional for
    # wine / malt — but if the application gave us a number, we check).
    if application.alcohol_content_pct is not None:
        verdicts["alcohol_content"] = check_alcohol_content(
            extracted_pct=label.alcohol_content_pct,
            extracted_text=label.alcohol_content_text,
            expected_pct=application.alcohol_content_pct,
            beverage=beverage,
        )

    # 4. Net contents (always required).
    verdicts["net_contents"] = check_net_contents(
        label.net_contents, application.net_contents, beverage
    )

    # 5. Bottler name (always required).
    verdicts["bottler_name"] = check_bottler_name(
        label.bottler_name, application.bottler_name, beverage
    )

    # 6. Bottler address (if provided in the application).
    if application.bottler_address:
        verdicts["bottler_address"] = check_bottler_address(
            label.bottler_address, application.bottler_address, beverage
        )

    # 7. Country of origin — only when the application declares an import.
    if application.is_import:
        verdicts["country_of_origin"] = check_country_of_origin(
            extracted=label.country_of_origin,
            expected=application.country_of_origin,
            is_import=True,
            beverage=beverage,
        )

    # 8. Government warning — required for every beverage type per §5.6.
    verdicts["government_warning"] = check_government_warning(
        label.government_warning_text.value,
        label.government_warning_formatting,
    )

    return verdicts
