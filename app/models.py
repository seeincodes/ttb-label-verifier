"""Pydantic schemas for the TTB Label Verification prototype.

Layered per presearch §5.2 / §5.4 / §5.5:

- `BeverageType`        — 27 CFR Parts 4 / 5 / 7 + a prototype catch-all.
- `ApplicationData`     — what the agent submits as the expected truth.
- `ExtractedField[T]`   — generic value + extraction confidence wrapper.
- `LabelData`           — what the vision model returned for one label.
- `Verdict`             — overall / per-field verdict taxonomy.
- `FieldVerdict`        — one verifier rule's output, with CFR citation.
- `VerificationResult`  — aggregate result returned to the UI / API.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Generic, Iterable, Literal, Optional, TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")

Confidence = Literal["high", "medium", "low"]


class BeverageType(str, Enum):
    """TTB-recognised beverage categories. Drives field conditionality in the
    verifier (presearch §5.6).

    - DISTILLED_SPIRITS — 27 CFR Part 5
    - WINE             — 27 CFR Part 4
    - MALT_BEVERAGE    — 27 CFR Part 7
    - OTHER            — prototype bucket for seltzers, RTDs, ciders ≥ 7 %
                         (universally required fields only)
    """

    DISTILLED_SPIRITS = "distilled_spirits"
    WINE = "wine"
    MALT_BEVERAGE = "malt_beverage"
    OTHER = "other"


class ApplicationData(BaseModel):
    """Expected truth submitted by the compliance agent.

    Per presearch §5.2. Beverage-type required-field matrix (§5.6) is enforced
    by the verifier, not by this schema — the schema only enforces *structural*
    invariants (is_import ↔ country_of_origin). That split keeps the JSON
    upload path forgiving and the verifier authoritative for citing 27 CFR.
    """

    beverage_type: BeverageType
    brand_name: str = Field(min_length=1)
    class_type: Optional[str] = None
    alcohol_content_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    net_contents: str = Field(min_length=1)
    bottler_name: str = Field(min_length=1)
    bottler_address: str = Field(min_length=1)
    is_import: bool = False
    country_of_origin: Optional[str] = None

    @model_validator(mode="after")
    def _import_country_consistency(self) -> "ApplicationData":
        if self.is_import and not self.country_of_origin:
            raise ValueError(
                "country_of_origin is required when is_import=True "
                "(import labels must declare origin per 27 CFR 5.66 / 4.39 / 7.66)"
            )
        if not self.is_import and self.country_of_origin:
            raise ValueError(
                "country_of_origin must be None when is_import=False "
                "(domestic labels cannot declare a foreign country of origin)"
            )
        return self


class ExtractedField(BaseModel, Generic[T]):
    """One field returned by the vision model, with per-field confidence.

    Shape per presearch §5.5: `{"value": <T or null>, "confidence": "high|medium|low"}`.

    Per-field confidence is the input to the MVP9 confidence gate — any
    *required* field at low confidence becomes a verdict of ERROR rather
    than risking a false PASS / FAIL. The prompt instructs the model to
    return `value=null` + `confidence="low"` rather than guess, so the
    `Optional[T]` is load-bearing, not cosmetic.
    """

    value: Optional[T] = None
    confidence: Confidence


class WarningFormatting(BaseModel):
    """Three-part formatting check on the government warning (27 CFR 16.22):
    caps on the phrase "GOVERNMENT WARNING", bold weight on that phrase,
    and continuous (non-interrupted) presentation. Asked of the vision
    model as three yes/no questions per presearch §5.1."""

    caps_correct: bool
    bold_correct: bool
    continuous: bool
    confidence: Confidence


class LabelData(BaseModel):
    """Vision-model output for a single label — the prompt contract from
    presearch §5.5. Every textual field is wrapped in `ExtractedField` so
    the verifier can apply the per-field confidence gate (MVP9).

    `alcohol_content_pct` (float) and `alcohol_content_text` (raw label
    string) are intentionally distinct: the verifier's numeric tolerance
    check uses the float, and the "ABV"-abbreviation regulatory check
    runs on the raw text. Conflating them would let an `Alc./Vol.` /
    `ABV` formatting violation slip past.
    """

    brand_name: ExtractedField[str]
    class_type: ExtractedField[str]
    alcohol_content_pct: ExtractedField[float]
    alcohol_content_text: ExtractedField[str]
    net_contents: ExtractedField[str]
    bottler_name: ExtractedField[str]
    bottler_address: ExtractedField[str]
    country_of_origin: ExtractedField[str]
    government_warning_text: ExtractedField[str]
    government_warning_formatting: WarningFormatting
    # Optional — added in the upload-prefill flow. Old payloads (pre-2026-05-21)
    # omit this; new prompts ask the model to suggest one of the four
    # BeverageType values. The agent's dropdown is still the source of truth;
    # this is a suggestion used to pre-populate the form.
    beverage_type_guess: Optional[BeverageType] = None


# Severity rank for `Verdict.worst_of`. Higher = worse. Kept as a module-level
# table rather than baked into the enum value so the wire format stays as a
# plain lowercase string (no IntEnum / tuple-value surprises in JSON).
_VERDICT_SEVERITY = {
    "pass":  0,
    "warn":  1,
    "fail":  2,
    "error": 3,
}


class Verdict(str, Enum):
    """Per-field and overall verdict (presearch §5.4).

    Severity: PASS < WARN < FAIL < ERROR. Overall verdict for a label =
    `Verdict.worst_of(field_verdicts)` — ERROR on a required field
    dominates any number of PASS / WARN / FAIL, which is the contract the
    MVP9 confidence gate relies on to avoid false PASS / FAIL when the
    image is unreadable.
    """

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"

    @property
    def severity(self) -> int:
        return _VERDICT_SEVERITY[self.value]

    @classmethod
    def worst_of(cls, verdicts: Iterable["Verdict"]) -> "Verdict":
        """Return the highest-severity verdict in `verdicts`.

        Raises ValueError if empty — a verifier run that produced zero
        field verdicts is a logic bug (the government-warning rule applies
        to every beverage type per §5.6), not a silent PASS.
        """
        materialised = list(verdicts)
        if not materialised:
            raise ValueError("worst_of() requires at least one verdict")
        return max(materialised, key=lambda v: v.severity)


class FieldVerdict(BaseModel):
    """One verifier rule's output for one field.

    Per presearch §5.7: every non-PASS verdict carries both a CFR citation
    and a human-readable reason. PASS may omit them (cosmetic-difference
    silent passes have no regulatory basis to cite).

    `evidence` is the raw comparison record (extracted vs expected, fuzzy
    score, ABV delta, etc.) — shape varies by rule, so it's a free dict.
    The verifier rules document their expected keys; the UI renders them
    in the audit-panel JSON view.
    """

    verdict: Verdict
    reason: str = ""
    cfr_citation: str = ""
    comparison_method: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _non_pass_needs_reason_and_citation(self) -> "FieldVerdict":
        if self.verdict is Verdict.PASS:
            return self
        if not self.reason.strip():
            raise ValueError(
                f"verdict={self.verdict.value} requires a non-empty reason "
                "(every WARN/FAIL/ERROR must be actionable per §5.7 / MVP4)"
            )
        # ERROR can come from the confidence gate, which is not itself a
        # CFR violation — but every WARN/FAIL has a regulatory basis.
        if self.verdict in (Verdict.WARN, Verdict.FAIL) and not self.cfr_citation.strip():
            raise ValueError(
                f"verdict={self.verdict.value} requires a cfr_citation "
                "(every regulatory WARN/FAIL cites the section it depends on)"
            )
        return self


class VerificationResult(BaseModel):
    """Aggregate response for a single label.

    Returned to the UI, stored in the LRU cache, and persisted to disk by
    the eval harness. `overall` is *derived* from `field_verdicts` via
    `Verdict.worst_of` — we re-validate that invariant here so a buggy
    caller can never assemble a result whose overall verdict disagrees
    with its parts (a silent regulatory false-PASS is the single worst
    failure mode for this product, per the FP-rate metric in MVP13).
    """

    overall: Verdict
    field_verdicts: dict[str, FieldVerdict]
    raw_extraction: LabelData
    cache_hit: bool = False
    fallback_used: bool = False
    extractor_used: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _overall_matches_worst_of_fields(self) -> "VerificationResult":
        if not self.field_verdicts:
            raise ValueError(
                "field_verdicts must be non-empty — every label runs at "
                "least the government-warning rule per §5.6"
            )
        expected = Verdict.worst_of(
            fv.verdict for fv in self.field_verdicts.values()
        )
        if self.overall is not expected:
            raise ValueError(
                f"overall={self.overall.value} disagrees with worst_of "
                f"field_verdicts={expected.value} — overall must be derived "
                "via Verdict.worst_of, never asserted independently"
            )
        return self
