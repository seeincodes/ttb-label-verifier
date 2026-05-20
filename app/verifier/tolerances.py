"""ABV tolerance lookup, per 27 CFR.

Per presearch §5.3:
  - Distilled spirits: ±0.3 pp                        — 27 CFR 5.65(b)
  - Malt beverages:    ±0.3 pp                        — 27 CFR 7.65(c)
  - Wine ≤14% ABV:     ±1.5 pp                        — 27 CFR 4.36
  - Wine >14% ABV:     ±1.0 pp                        — 27 CFR 4.36
  - OTHER (seltzers, RTDs, cider ≥7%): treated as ±0.3 pp by analogy
    to spirits; never use a wine tolerance for non-wine labels.

The 14 % wine boundary is regulatory, not arbitrary — getting it
backwards (treating > 14 % as the wider ±1.5 pp band) would turn a real
violation into a silent PASS. That's the worst failure mode for this
product (MVP13 FP-rate metric), so the boundary is pinned by an explicit
unit test (`test_wine_boundary_at_14_uses_low_band`).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import BeverageType


@dataclass(frozen=True, slots=True)
class Tolerance:
    """The tolerance applicable to one (beverage, expected_abv) pair.

    `pp` is the half-width in percentage points (i.e. |extracted -
    expected| ≤ `pp` is PASS). `cfr_citation` is the section the rule's
    FAIL / WARN reason will quote — kept on the same object so callers
    can't forget to cite the source they used.
    """

    pp: float
    cfr_citation: str


# Wine band boundary — inclusive on the ≤14 side per 27 CFR 4.36.
_WINE_BOUNDARY_ABV: float = 14.0


def tolerance_for(beverage: BeverageType, expected_abv: float) -> Tolerance:
    """Return the applicable ABV tolerance and citation.

    Args:
        beverage: BeverageType of the label.
        expected_abv: expected ABV in percent (e.g. 12.5 for 12.5%).

    Raises:
        ValueError: when `expected_abv` is negative — a caller bug we'd
            rather surface loudly than silently apply a band to.

    Citations:
        - 27 CFR 5.65(b) — distilled spirits flat ±0.3 pp.
        - 27 CFR 7.65(c) — malt beverages flat ±0.3 pp.
        - 27 CFR 4.36   — wine: ±1.5 pp at ≤14% ABV, ±1.0 pp above 14%.
    """
    if expected_abv < 0:
        raise ValueError(
            f"expected_abv must be non-negative, got {expected_abv!r}"
        )

    if beverage is BeverageType.DISTILLED_SPIRITS:
        return Tolerance(pp=0.3, cfr_citation="27 CFR 5.65(b)")

    if beverage is BeverageType.MALT_BEVERAGE:
        return Tolerance(pp=0.3, cfr_citation="27 CFR 7.65(c)")

    if beverage is BeverageType.WINE:
        if expected_abv <= _WINE_BOUNDARY_ABV:
            return Tolerance(pp=1.5, cfr_citation="27 CFR 4.36")
        return Tolerance(pp=1.0, cfr_citation="27 CFR 4.36")

    # OTHER — seltzers / RTDs / ciders ≥7%. Use the strict ±0.3 pp band
    # and cite the spirits rule by analogy. Never apply a wine tolerance
    # to a non-wine label.
    return Tolerance(pp=0.3, cfr_citation="27 CFR 5.65(b) (by analogy)")
