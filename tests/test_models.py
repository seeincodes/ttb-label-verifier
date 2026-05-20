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
