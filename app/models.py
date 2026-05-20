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
from typing import Optional

from pydantic import BaseModel, Field, model_validator


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
