"""Extraction prompt builder — the wire contract for every vision provider.

The prompt is provider-agnostic by design: Gemini and OpenAI both receive
the same text and are expected to return the same JSON shape (presearch §5.5).
Keeping the prompt in one place means a tweak (e.g. tightening the
'do not guess' instruction after an eval-suite false-PASS) lands once.

Contract enforced by tests in `tests/test_extraction_prompt.py`:

- Every top-level key from §5.5 appears in the prompt body.
- The model is *explicitly* instructed to return null + "low" rather than guess
  (MVP9 confidence gate depends on the model being honest about uncertainty).
- The three §5.1 government-warning yes/no questions are present.
- Beverage type is parameterised into the prompt — wine vs spirits vs malt
  shifts which fields are required (§5.6).
- A well-formed JSON example is embedded (models mimic examples; the example
  must itself parse).
- Markdown / code fences are forbidden in the response (mitigates the
  Gemini JSON-mode string-wrap quirk noted in ERROR_FIX_LOG).
"""
from __future__ import annotations

import json
from textwrap import dedent

from app.models import BeverageType


# Human-readable beverage descriptions for the prompt header. The values must
# also be present as substrings (`spirits`, `wine`, etc.) so the
# beverage-type-surfacing test can assert on them.
_BEVERAGE_HEADERS = {
    BeverageType.DISTILLED_SPIRITS: (
        "distilled_spirits (whiskey, vodka, gin, rum, tequila, brandy, "
        "liqueur, etc. — 27 CFR Part 5)"
    ),
    BeverageType.WINE: (
        "wine (still, sparkling, fortified, dessert — 27 CFR Part 4)"
    ),
    BeverageType.MALT_BEVERAGE: (
        "malt_beverage (beer, ale, lager, stout, porter, malt liquor — "
        "27 CFR Part 7)"
    ),
    BeverageType.OTHER: (
        "other (hard seltzer, RTD cocktails, cider ≥7% ABV — universal "
        "TTB labelling rules only)"
    ),
}


# A literal example response shown to the model. Kept here so the test suite
# can re-parse it and confirm we never ship a typo. Values are illustrative,
# not actual labels.
_EXAMPLE_RESPONSE = {
    "brand_name": {"value": "OLD TOM DISTILLERY", "confidence": "high"},
    "class_type": {
        "value": "Kentucky Straight Bourbon Whiskey",
        "confidence": "high",
    },
    "alcohol_content_pct": {"value": 45.0, "confidence": "high"},
    "alcohol_content_text": {
        "value": "45% ALC./VOL. (90 PROOF)",
        "confidence": "high",
    },
    "net_contents": {"value": "750 mL", "confidence": "high"},
    "bottler_name": {"value": "Old Tom Distillery LLC", "confidence": "high"},
    "bottler_address": {
        "value": "123 Distillery Rd, Frankfort, KY 40601",
        "confidence": "medium",
    },
    "country_of_origin": {"value": None, "confidence": "high"},
    "government_warning_text": {
        "value": (
            "GOVERNMENT WARNING: (1) According to the Surgeon General, "
            "women should not drink alcoholic beverages during pregnancy "
            "because of the risk of birth defects. (2) Consumption of "
            "alcoholic beverages impairs your ability to drive a car or "
            "operate machinery, and may cause health problems."
        ),
        "confidence": "high",
    },
    "government_warning_formatting": {
        "caps_correct": True,
        "bold_correct": True,
        "continuous": True,
        "confidence": "high",
    },
    "beverage_type_guess": "distilled_spirits",
}


def build_extraction_prompt(beverage_type: BeverageType) -> str:
    """Build the per-beverage-type extraction prompt.

    Same prompt body for every vision provider — the abstraction signal of
    keeping the contract uniform is more valuable than per-provider tuning
    at prototype scale.
    """
    header = _BEVERAGE_HEADERS[beverage_type]
    example_json = json.dumps(_EXAMPLE_RESPONSE, indent=2)

    return dedent(
        f"""\
        You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) label-extraction
        assistant. The image is one alcohol-beverage container label. The beverage
        type is: {header}.

        Read the label and return one JSON object with the exact shape below.
        Return only JSON — no markdown, no code fences, no commentary.

        For every field, return an object with two keys:
          - "value":      the extracted value, or null if not visible / not present
                          on the label.
          - "confidence": one of "high", "medium", or "low".

        Confidence rules (these are load-bearing — a downstream confidence gate
        treats any required field at "low" as ERROR rather than risk a false
        PASS or FAIL):
          - "high"   — you can clearly read the field on the label.
          - "medium" — readable but with some ambiguity (e.g. partial occlusion).
          - "low"    — cannot read reliably. Return value=null with confidence=low
                       rather than guess. Do not guess. Never guess. If unsure,
                       say "low".

        Required top-level keys (exactly these — no extras):
          brand_name, class_type, alcohol_content_pct, alcohol_content_text,
          net_contents, bottler_name, bottler_address, country_of_origin,
          government_warning_text, government_warning_formatting,
          beverage_type_guess.

        Field guidance:
          - alcohol_content_pct: the numeric percentage as a float (e.g. 45.0).
          - alcohol_content_text: the literal text on the label verbatim
            (e.g. "45% ALC./VOL." or "Alc. 5% by Vol."). Do not normalise —
            the downstream verifier checks for the forbidden "ABV" abbreviation
            on this exact string.
          - country_of_origin: value=null with confidence=high if the label is
            domestic (no country printed). Only fill in a value if a country
            of origin is actually visible on the label.
          - government_warning_text: the full warning text verbatim from the
            label (used for an exact-match check against 27 CFR 16.21).

        beverage_type_guess is a plain string (no value/confidence wrapper)
        with one of exactly these four values, your best read of the label's
        regulatory category:
          - "distilled_spirits" — whiskey, vodka, gin, rum, tequila, brandy,
                                  liqueur, etc.
          - "wine"              — still / sparkling / fortified / dessert wine
          - "malt_beverage"     — beer, ale, lager, stout, porter, malt liquor
          - "other"             — hard seltzer, RTD cocktails, cider, anything
                                  that doesn't fit the three above.
        This is a suggestion used to pre-populate a form for the agent — the
        agent confirms the final classification.

        government_warning_formatting is a different shape — four keys, all
        required, with no value/confidence wrapper around each key:
          - caps_correct  (bool): Is the phrase "GOVERNMENT WARNING" rendered
                                  in ALL CAPITAL LETTERS on the label?
          - bold_correct  (bool): Is "GOVERNMENT WARNING" rendered in
                                  bold / heavier weight than the rest of
                                  the warning text?
          - continuous    (bool): Does the warning appear as one continuous
                                  uninterrupted statement, not broken up by
                                  other content / images / pricing?
          - confidence    (str):  "high" | "medium" | "low" for your overall
                                  confidence in the three answers above.

        Example response (illustrative — copy the *shape*, not the values):

        {example_json}
        """
    )
