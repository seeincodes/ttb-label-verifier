"""Tests for app.verifier.tolerances.

Per presearch §5.3 / 27 CFR 5.65 / 7.65 / 4.36. The wine boundary at 14%
is regulatory, not arbitrary — getting it backwards (>14 → ±1.5) would
turn a violation into a silent PASS, which is the worst failure mode for
this product (MVP13 FP-rate metric)."""
from __future__ import annotations

import pytest

from app.models import BeverageType


class TestToleranceFor:
    @pytest.mark.parametrize(
        "beverage,expected_abv,expected_tol,expected_citation",
        [
            # Spirits — flat ±0.3 pp regardless of ABV (5.65(b)).
            (BeverageType.DISTILLED_SPIRITS, 40.0, 0.3, "27 CFR 5.65(b)"),
            (BeverageType.DISTILLED_SPIRITS, 80.0, 0.3, "27 CFR 5.65(b)"),
            # Malt — flat ±0.3 pp (7.65(c)).
            (BeverageType.MALT_BEVERAGE, 5.0, 0.3, "27 CFR 7.65(c)"),
            (BeverageType.MALT_BEVERAGE, 0.5, 0.3, "27 CFR 7.65(c)"),
            # Wine ≤14% — ±1.5 pp (4.36).
            (BeverageType.WINE, 12.5, 1.5, "27 CFR 4.36"),
            (BeverageType.WINE, 14.0, 1.5, "27 CFR 4.36"),
            # Wine >14% — ±1.0 pp (4.36).
            (BeverageType.WINE, 14.01, 1.0, "27 CFR 4.36"),
            (BeverageType.WINE, 20.0, 1.0, "27 CFR 4.36"),
        ],
    )
    def test_returns_correct_tolerance_and_citation(
        self, beverage, expected_abv, expected_tol, expected_citation
    ):
        from app.verifier.tolerances import tolerance_for

        tol = tolerance_for(beverage, expected_abv)
        assert tol.pp == pytest.approx(expected_tol)
        assert tol.cfr_citation == expected_citation

    def test_wine_boundary_at_14_uses_low_band(self):
        """Exactly 14.0 → ±1.5 pp (the ≤ 14 band). Off-by-one here would
        turn a 14% table-wine FAIL into a silent PASS — the test pins
        the boundary explicitly so future refactors don't drift it."""
        from app.verifier.tolerances import tolerance_for

        boundary = tolerance_for(BeverageType.WINE, 14.0)
        just_over = tolerance_for(BeverageType.WINE, 14.0001)
        assert boundary.pp == pytest.approx(1.5)
        assert just_over.pp == pytest.approx(1.0)

    def test_other_beverage_uses_strict_tolerance(self):
        """OTHER is the seltzer / RTD / cider ≥ 7 % catch-all; lean spirits-
        like. Use the strict ±0.3 pp band and cite 5.65(b) by analogy
        (the verifier never auto-applies a wine tolerance to seltzers)."""
        from app.verifier.tolerances import tolerance_for

        tol = tolerance_for(BeverageType.OTHER, 7.5)
        assert tol.pp == pytest.approx(0.3)
        # citation should reference the strict-band rule even though OTHER
        # is a prototype bucket — never a wine cite for a non-wine label.
        assert "4.36" not in tol.cfr_citation

    def test_negative_abv_rejected(self):
        """expected_abv should be a real label value. A negative is a
        caller bug — raise so it surfaces, never silently 'tolerate'."""
        from app.verifier.tolerances import tolerance_for

        with pytest.raises(ValueError):
            tolerance_for(BeverageType.WINE, -1.0)

    def test_tolerance_returns_named_struct(self):
        """Callers need both the pp value and the citation. A flat float
        forces a parallel lookup; a named structure keeps them coupled."""
        from app.verifier.tolerances import tolerance_for

        tol = tolerance_for(BeverageType.DISTILLED_SPIRITS, 40.0)
        # Either an attrs / NamedTuple / dataclass — but `.pp` and
        # `.cfr_citation` must exist as named attributes, not dict keys.
        assert hasattr(tol, "pp")
        assert hasattr(tol, "cfr_citation")
