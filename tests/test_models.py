"""Tests for app.models — Pydantic schemas (Task Group 2)."""
from __future__ import annotations

import pytest


class TestBeverageType:
    """27 CFR Parts 4 (wine), 5 (spirits), 7 (malt). 'other' is the prototype
    catch-all for seltzers / RTDs / ciders ≥ 7 % per presearch §5.2."""

    def test_has_four_string_values(self):
        from app.models import BeverageType

        assert BeverageType.DISTILLED_SPIRITS.value == "distilled_spirits"
        assert BeverageType.WINE.value == "wine"
        assert BeverageType.MALT_BEVERAGE.value == "malt_beverage"
        assert BeverageType.OTHER.value == "other"

    def test_is_str_subclass_for_json_round_trip(self):
        """BaseModel(beverage_type='wine') must coerce a raw string in. This
        is what `ApplicationData` parsing from a JSON upload depends on."""
        from app.models import BeverageType

        assert isinstance(BeverageType.WINE, str)
        assert BeverageType("wine") is BeverageType.WINE

    def test_unknown_value_raises(self):
        from app.models import BeverageType

        with pytest.raises(ValueError):
            BeverageType("beer")  # canonical value is malt_beverage


class TestApplicationData:
    """Per presearch §5.2. The schema only enforces *structural* invariants
    (is_import ↔ country_of_origin). Beverage-type field-requirement matrix
    (presearch §5.6) is enforced by the verifier, not the schema — that
    keeps the JSON-upload path forgiving and the verifier authoritative."""

    def _minimal_domestic(self, **overrides):
        from app.models import ApplicationData, BeverageType

        base = dict(
            beverage_type=BeverageType.DISTILLED_SPIRITS,
            brand_name="Old Tom Distillery",
            class_type="Kentucky Straight Bourbon Whiskey",
            alcohol_content_pct=45.0,
            net_contents="750 mL",
            bottler_name="Old Tom Distillery LLC",
            bottler_address="123 Distillery Rd, Frankfort, KY 40601",
            is_import=False,
        )
        base.update(overrides)
        return ApplicationData(**base)

    def test_minimal_domestic_spirits_application_constructs(self):
        app = self._minimal_domestic()
        assert app.beverage_type.value == "distilled_spirits"
        assert app.brand_name == "Old Tom Distillery"
        assert app.country_of_origin is None
        assert app.is_import is False

    def test_optional_class_type_and_abv_default_to_none(self):
        """class_type / alcohol_content_pct are Optional at the schema layer
        even though spirits *require* them (§5.6). Verifier enforces, not Pydantic."""
        from app.models import ApplicationData, BeverageType

        app = ApplicationData(
            beverage_type=BeverageType.OTHER,
            brand_name="Hard Seltzer Co",
            net_contents="355 mL",
            bottler_name="Hard Seltzer Co",
            bottler_address="1 Main St",
        )
        assert app.class_type is None
        assert app.alcohol_content_pct is None
        assert app.is_import is False
        assert app.country_of_origin is None

    def test_import_requires_country_of_origin(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            self._minimal_domestic(is_import=True, country_of_origin=None)
        # error message must point a developer (or the UI) at the offending field
        assert "country_of_origin" in str(exc_info.value).lower()

    def test_domestic_must_not_have_country_of_origin(self):
        """is_import=False with country_of_origin set is contradictory and
        would otherwise let a confused agent submit nonsense expected data."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._minimal_domestic(is_import=False, country_of_origin="Scotland")

    def test_import_with_country_passes(self):
        app = self._minimal_domestic(
            is_import=True, country_of_origin="Scotland"
        )
        assert app.is_import is True
        assert app.country_of_origin == "Scotland"

    def test_brand_name_required(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._minimal_domestic(brand_name=None)

    def test_accepts_raw_string_beverage_type_from_json(self):
        """A JSON upload posts beverage_type as a string. Pydantic must coerce
        it via the str-Enum subclass without a custom validator."""
        from app.models import ApplicationData

        app = ApplicationData(
            beverage_type="wine",
            brand_name="Vineyard X",
            class_type="Table Wine",
            alcohol_content_pct=12.5,
            net_contents="750 mL",
            bottler_name="Vineyard X",
            bottler_address="42 Vine St",
        )
        assert app.beverage_type.value == "wine"


class TestExtractedField:
    """Generic wrapper for vision-model output, per presearch §5.5.

    Shape: {"value": <T or null>, "confidence": "high|medium|low"}.
    Per-field confidence is the input to the verifier's confidence gate
    (MVP9): any *required* field at low confidence becomes ERROR."""

    def test_high_confidence_string_value(self):
        from app.models import ExtractedField

        f = ExtractedField[str](value="OLD TOM DISTILLERY", confidence="high")
        assert f.value == "OLD TOM DISTILLERY"
        assert f.confidence == "high"

    def test_float_parametrisation(self):
        from app.models import ExtractedField

        f = ExtractedField[float](value=45.0, confidence="high")
        assert f.value == pytest.approx(45.0)

    def test_null_value_with_low_confidence_allowed(self):
        """The prompt explicitly tells the model to return value=null +
        confidence=low rather than guess — verifier needs this path open."""
        from app.models import ExtractedField

        f = ExtractedField[str](value=None, confidence="low")
        assert f.value is None
        assert f.confidence == "low"

    def test_invalid_confidence_rejected(self):
        from pydantic import ValidationError

        from app.models import ExtractedField

        with pytest.raises(ValidationError):
            ExtractedField[str](value="x", confidence="HIGH")  # case-sensitive
        with pytest.raises(ValidationError):
            ExtractedField[str](value="x", confidence="medium-ish")

    def test_round_trip_through_json(self):
        """The verifier reads this JSON directly off the vision model's
        response, so the field-by-field round-trip must be lossless."""
        from app.models import ExtractedField

        original = ExtractedField[str](value="bourbon", confidence="medium")
        parsed = ExtractedField[str].model_validate_json(
            original.model_dump_json()
        )
        assert parsed.value == "bourbon"
        assert parsed.confidence == "medium"


# Sample vision-model JSON exactly matching the prompt contract in
# presearch §5.5. Reused across LabelData tests so we test parsing of the
# real wire shape, not a mock dict shape.
GEMINI_SAMPLE_JSON = """
{
  "brand_name":           {"value": "OLD TOM DISTILLERY", "confidence": "high"},
  "class_type":           {"value": "Kentucky Straight Bourbon Whiskey", "confidence": "high"},
  "alcohol_content_pct":  {"value": 45.0, "confidence": "high"},
  "alcohol_content_text": {"value": "45% ALC./VOL. (90 PROOF)", "confidence": "high"},
  "net_contents":         {"value": "750 mL", "confidence": "high"},
  "bottler_name":         {"value": "Old Tom Distillery LLC", "confidence": "medium"},
  "bottler_address":      {"value": "123 Distillery Rd, Frankfort, KY", "confidence": "low"},
  "country_of_origin":    {"value": null, "confidence": "high"},
  "government_warning_text": {"value": "GOVERNMENT WARNING: ...", "confidence": "high"},
  "government_warning_formatting": {
    "caps_correct": true,
    "bold_correct": true,
    "continuous":   true,
    "confidence":   "high"
  }
}
"""


class TestWarningFormatting:
    """Per presearch §5.1 the formatting block is a dedicated three-question
    structure asked of the vision model, separate from the warning *text*
    extraction. Citation: 27 CFR 16.22 (caps, bold, continuous)."""

    def test_all_three_questions_required(self):
        from app.models import WarningFormatting

        wf = WarningFormatting(
            caps_correct=True,
            bold_correct=True,
            continuous=True,
            confidence="high",
        )
        assert wf.caps_correct is True
        assert wf.bold_correct is True
        assert wf.continuous is True
        assert wf.confidence == "high"

    def test_missing_question_rejected(self):
        from pydantic import ValidationError

        from app.models import WarningFormatting

        with pytest.raises(ValidationError):
            WarningFormatting(
                caps_correct=True, bold_correct=True, confidence="high"
            )

    def test_failing_formatting_still_validates(self):
        """Verifier consumes this — failing formatting must be parseable
        so we can FAIL the label, not crash on parse."""
        from app.models import WarningFormatting

        wf = WarningFormatting(
            caps_correct=False,
            bold_correct=True,
            continuous=True,
            confidence="medium",
        )
        assert wf.caps_correct is False


class TestLabelData:
    """The full per-field per-confidence extraction (presearch §5.5).
    Verifier reads this as-is — the prompt contract is *exact*, not negotiable."""

    def test_parses_canonical_gemini_payload(self):
        from app.models import LabelData

        data = LabelData.model_validate_json(GEMINI_SAMPLE_JSON)

        assert data.brand_name.value == "OLD TOM DISTILLERY"
        assert data.brand_name.confidence == "high"
        assert data.alcohol_content_pct.value == pytest.approx(45.0)
        assert data.alcohol_content_text.value == "45% ALC./VOL. (90 PROOF)"
        assert data.bottler_address.confidence == "low"
        assert data.country_of_origin.value is None
        assert data.government_warning_formatting.caps_correct is True

    def test_alcohol_content_text_is_separate_from_pct(self):
        """The verifier checks the literal "ABV" substring on the *text*
        version, not on the numeric pct — so the schema must keep them
        as two independent fields."""
        from app.models import LabelData

        data = LabelData.model_validate_json(GEMINI_SAMPLE_JSON)
        assert data.alcohol_content_pct.value != data.alcohol_content_text.value

    def test_missing_required_field_rejected(self):
        """The prompt contract is exact. A model response missing
        government_warning_formatting (most important field for §5.1)
        must fail parse so we never silently skip the formatting check."""
        import json

        from pydantic import ValidationError

        from app.models import LabelData

        payload = json.loads(GEMINI_SAMPLE_JSON)
        del payload["government_warning_formatting"]
        with pytest.raises(ValidationError):
            LabelData.model_validate(payload)

    def test_round_trip_lossless(self):
        from app.models import LabelData

        data = LabelData.model_validate_json(GEMINI_SAMPLE_JSON)
        again = LabelData.model_validate_json(data.model_dump_json())
        assert again == data


class TestVerdict:
    """Severity ordering per presearch §5.4: ERROR > FAIL > WARN > PASS.
    Overall verdict is the worst across all checked field verdicts."""

    def test_four_string_values(self):
        from app.models import Verdict

        assert Verdict.PASS.value == "pass"
        assert Verdict.WARN.value == "warn"
        assert Verdict.FAIL.value == "fail"
        assert Verdict.ERROR.value == "error"

    def test_severity_ordering(self):
        from app.models import Verdict

        assert Verdict.PASS.severity < Verdict.WARN.severity
        assert Verdict.WARN.severity < Verdict.FAIL.severity
        assert Verdict.FAIL.severity < Verdict.ERROR.severity

    def test_worst_of_picks_highest_severity(self):
        """The verifier aggregates per-field verdicts by `worst_of`. ERROR on
        any required field must dominate any number of PASS / WARN / FAIL."""
        from app.models import Verdict

        assert Verdict.worst_of([Verdict.PASS]) is Verdict.PASS
        assert Verdict.worst_of([Verdict.PASS, Verdict.PASS]) is Verdict.PASS
        assert Verdict.worst_of([Verdict.PASS, Verdict.WARN]) is Verdict.WARN
        assert (
            Verdict.worst_of([Verdict.WARN, Verdict.FAIL, Verdict.WARN])
            is Verdict.FAIL
        )
        assert (
            Verdict.worst_of(
                [Verdict.PASS, Verdict.FAIL, Verdict.ERROR, Verdict.WARN]
            )
            is Verdict.ERROR
        )

    def test_worst_of_empty_raises(self):
        """An empty verdict list means the verifier ran zero rules — that's
        a logic bug (every label has at least the warning rule), not a PASS."""
        from app.models import Verdict

        with pytest.raises(ValueError):
            Verdict.worst_of([])

    def test_is_str_subclass_for_json(self):
        """Verdict serialises to a plain lowercase string in API responses."""
        from app.models import Verdict

        assert isinstance(Verdict.FAIL, str)
        assert Verdict("error") is Verdict.ERROR
