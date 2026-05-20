"""String / volume normalization helpers for the verifier.

Each helper is a small pure function so rules compose them. The rules layer
chains `strip_corporate_suffixes` → `normalize_text` → `rapidfuzz` for
bottler names, and `normalize_volume` + `volumes_equivalent` for net
contents (presearch §5.4).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

# Apostrophes (straight + curly + backtick) are deleted, not spaced — so
# "Stone's" and "STONE'S" both collapse to "stones", which is what the
# §5.4 cosmetic-difference path requires.
_APOSTROPHES = "'’‘`ʼ"
# All other punctuation becomes a space; whitespace then collapses. This
# keeps multi-word strings multi-word ("Stone—Throw" → "stone throw")
# without re-introducing the apostrophe edge case.
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    The output is the input to `rapidfuzz.token_sort_ratio`. The §5.4
    cosmetic-difference path depends on this:
      - case-insensitive (STONE → stone)
      - apostrophe-insensitive AS DELETION ("Stone's" → "stones") so the
        possessive doesn't introduce a spurious word boundary
      - other punctuation (em-dash, period, slash, comma) replaced with a
        space so genuine word boundaries survive
    """
    if not s:
        return ""
    # Step 1: drop apostrophes outright.
    cleaned = "".join(ch for ch in s if ch not in _APOSTROPHES)
    # Step 2: any remaining Unicode-punctuation codepoint becomes a space.
    cleaned = "".join(
        " " if unicodedata.category(ch).startswith("P") else ch
        for ch in cleaned
    )
    # Step 3: collapse whitespace and lowercase.
    return _WHITESPACE_RE.sub(" ", cleaned).strip().lower()


# ---------------------------------------------------------------------------
# Volume normalization
# ---------------------------------------------------------------------------

_ML_PER_FL_OZ = 29.5735  # US fluid ounce, NIST.

# Matches: "750 mL", "750ml", "0.75 L", "0.75l", "1.5 L", "12 fl oz",
# "12 FL. OZ.". Volume number captured group, unit captured group.
_VOLUME_RE = re.compile(
    r"""
    ^\s*
    (?P<value>\d+(?:\.\d+)?)        # 750 or 0.75
    \s*
    (?P<unit>ml|l|fl\.?\s*oz\.?)    # mL | L | fl oz
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_volume(s: str) -> Optional[float]:
    """Parse a label volume string into milliliters.

    Returns None when the string doesn't match a recognised pattern, so
    the rule layer can FAIL with an actionable reason ("net contents
    unparseable — extracted value: 'bottle'") rather than silently
    passing or crashing.
    """
    if not s:
        return None
    m = _VOLUME_RE.match(s)
    if not m:
        return None
    value = float(m.group("value"))
    unit = m.group("unit").lower().replace(" ", "").replace(".", "")
    if unit == "ml":
        return value
    if unit == "l":
        return value * 1000.0
    if unit == "floz":
        return value * _ML_PER_FL_OZ
    return None  # pragma: no cover — regex restricts to the three units


def volumes_equivalent(a: str, b: str, tolerance_pct: float = 0.5) -> bool:
    """True if `a` and `b` refer to the same volume within `tolerance_pct`.

    Default tolerance is 0.5 % — covers TTB rounding to whole mL on
    volumes ≤ 1 L. Any unparseable input returns False so the caller
    can surface a meaningful error rather than silently passing on
    garbage; if both sides are exactly equal as strings they compare
    equal before we attempt to parse.
    """
    if a == b and a:
        return True
    ml_a = normalize_volume(a)
    ml_b = normalize_volume(b)
    if ml_a is None or ml_b is None or ml_a == 0:
        return False
    return abs(ml_a - ml_b) / ml_a * 100 <= tolerance_pct


# ---------------------------------------------------------------------------
# Bottler-name corporate suffixes
# ---------------------------------------------------------------------------

# Sorted longest-first so 'Corporation' wins over 'Corp' when both could
# match. Patterns are case-insensitive and anchored to end-of-string.
_CORP_SUFFIXES = (
    "Corporation",
    "Company",
    "Limited",
    "Corp",
    "Inc",
    "Co",
    "Ltd",
    "L.L.C.",
    "LLC",
)


def strip_corporate_suffixes(s: str) -> str:
    """Remove a trailing entity suffix (LLC, Inc., Co., etc.).

    Only strips *trailing* suffixes — 'Company Distillers Inc' becomes
    'Company Distillers', not 'Distillers'. Used for bottler comparison
    so "Old Tom Distillery LLC" vs "Old Tom Distillery" aligns silently
    (presearch §5.4 cosmetic-difference path).
    """
    if not s:
        return s
    stripped = s.rstrip()
    for suffix in _CORP_SUFFIXES:
        # The suffix itself, optionally preceded by a comma+space, with
        # optional trailing period.
        pattern = rf",?\s+{re.escape(suffix)}\.?\s*$"
        new = re.sub(pattern, "", stripped, count=1, flags=re.IGNORECASE)
        if new != stripped:
            return new.rstrip()
    return s
