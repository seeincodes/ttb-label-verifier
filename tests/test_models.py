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
