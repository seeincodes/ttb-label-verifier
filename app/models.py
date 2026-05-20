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
