# Eval test-set generation notes

This document is the reproducibility record for the eval fixtures in
`eval/test_set/labels/` + `eval/test_set/expected/`. The fixtures are
**synthetic JSON**, not real label photographs — they exercise the
deterministic verifier path (presearch §6.1).

## Why synthetic JSON, not real images

The brief asks for "~20 labels"; the project's locked stack prohibits
persistence of real label image data (PII / federal compliance concern,
documented in `docs/MEMO.md` §10). The verifier is the audit-relevant
artifact; the extractor is interchangeable behind `LabelExtractor`. So
the eval suite exercises the **verifier** deterministically by feeding
it pre-canned `LabelData` JSON (the same shape the vision model would
produce), and reports the metrics in presearch §6.2.

Real-image eval requires running the live extractor through each label
and capturing latency / cost / cache-hit. The harness has a
fixture-mode-vs-real-mode switch that's documented but not wired for
this take-home — the fixtures bypass the extractor entirely. See
`eval/harness.py` for the comment block; see `make eval` for the
fixture-mode CLI.

## Fixture buckets

Four buckets, five fixtures each (20 total) per presearch §6.1.

### `easy` (5) — clean, all-PASS

Designed to confirm the verifier doesn't *spuriously* fail well-formed
labels across all four beverage types.

| Fixture                       | Beverage           | Notes |
| ----------------------------- | ------------------ | ----- |
| `easy_spirits_bourbon`        | distilled_spirits  | The canonical happy-path Kentucky Straight Bourbon Whiskey label. |
| `easy_wine_chardonnay`        | wine               | Domestic table wine ≤14% — exercises the wide tolerance band. |
| `easy_malt_lager`             | malt_beverage      | Class/type optional for malt per §5.6 — exercises the silent-skip. |
| `easy_other_seltzer`          | other              | Hard seltzer; OTHER bucket is the seltzer/RTD catch-all. |
| `easy_spirits_import`         | distilled_spirits  | `is_import=true` + matching country of origin — exercises the country rule. |

### `hard` (5) — image-quality challenges → ERROR or WARN

Designed to confirm the **MVP9 confidence gate** behaves correctly when
the model honored the prompt's "null + low confidence rather than guess"
instruction.

| Fixture                                | Expected verdict | What it tests |
| -------------------------------------- | ---------------- | ------------- |
| `hard_blurry_brand`                    | error            | Required field (brand_name) at low confidence → field ERROR → overall ERROR. |
| `hard_blurry_warning_formatting`       | error            | Warning *formatting* confidence is low; check_warning_formatting yields ERROR, not PASS or FAIL. |
| `hard_low_class_for_malt`              | warn             | Class/type optional for malt; low confidence with app-supplied expected → WARN ("unverifiable"), never ERROR. |
| `hard_blurry_net_contents`             | error            | Net contents required for every beverage type; low confidence + value=null → ERROR. |
| `hard_blurry_abv_text`                 | error            | ABV pct and ABV text both at low confidence; required for spirits → ERROR. |

### `violations` (5) — regulatory violations → FAIL with CFR citation

Designed to confirm the verifier catches each major regulatory failure
mode AND that the FAIL reason carries the correct 27 CFR section. These
are the false-PASS-prevention tests — the FP-rate metric should be 0
across this bucket.

| Fixture                              | CFR cite      | What it tests |
| ------------------------------------ | ------------- | ------------- |
| `violations_abv_abbreviation`        | 5.65(b)       | Literal "ABV" substring on label is prohibited even when the numeric value matches. |
| `violations_abv_over_tolerance`      | 5.65(b)       | Spirits ABV 3.5pp over expected — well past 2× the ±0.3pp band → FAIL with delta in reason. |
| `violations_wrong_country`           | 5.36(d)       | Import label declares Ireland; application says Scotland. |
| `violations_warning_missing_clause`  | 16.21         | Warning text missing clause (2); text layer FAILs while formatting layer would PASS. |
| `violations_warning_formatting`      | 16.22         | Warning text canonical but "Government Warning" rendered in title case (caps_correct=false). |

### `edge_cases` (6) — boundary conditions

Designed to confirm the verifier handles the §5.4 silent-PASS paths
correctly (cosmetic difference, unit equivalence, corp suffix), the
regulatory tolerance boundaries, and the §4.21 class-vs-ABV consistency
rule that catches what tolerance alone misses.

| Fixture                              | Expected verdict | What it tests |
| ------------------------------------ | ---------------- | ------------- |
| `edge_borderline_fuzzy_brand`        | warn             | Brand "Old Tom Distillers" vs "Old Tom Distillery" — fuzzy 80–94 band → WARN with score. |
| `edge_wine_14pct_boundary`           | pass             | Wine at exactly 14.0% — pinned by the wine-boundary test in `tests/test_tolerances.py`; the ≤14 band applies inclusively. |
| `edge_wine_just_over_boundary`       | pass             | Fortified wine at 16.5% — exercises the >14 tolerance branch (±1.0pp). |
| `edge_corp_suffix_variant`           | pass             | Bottler "Stone's Throw Distillery, Inc." vs "...Distillery LLC" + brand "STONE'S THROW" vs "Stone's Throw" — two §5.4 cosmetic paths in one fixture. |
| `edge_volume_unit_equivalent`        | pass             | Label "0.75 L" vs application "750 mL" — silent-PASS via normalize_volume. |
| `edge_table_wine_above_14pct`        | fail             | STR6 / 27 CFR 4.21 — 14.5% wine labelled "Table Wine" against a COLA at 13.0%. The numeric ABV is within the ±1.5pp §4.36 tolerance, so an ABV-only verifier would silently PASS. The class-designation rule catches it: §4.21 defines table wine as ≤14% ABV. This is the regulatory subtlety presearch §2 flagged as Dave-Morrison-grade. |

## Reproducibility

Every fixture is hand-authored against the §5.5 prompt contract. No AI
image generation was used — the entire eval suite is text-only JSON.
The fixture's `expected_overall` is the authoritative claim; running
`make eval` exercises the verifier and surfaces any drift between
fixture and verifier.

When real label images replace these fixtures, the **expected** records
(`eval/test_set/expected/*.json`) carry forward unchanged — the
`application` block and `expected_overall` are independent of how the
`LabelData` was produced. Drop the labels JSON, point the harness at
the real extractor, and the same metrics math applies.

## How to add a new fixture

1. Decide which bucket (easy / hard / violations / edge_cases).
2. Write `eval/test_set/labels/<bucket>_<short_name>.json` — the
   `LabelData` JSON in the §5.5 shape. Mirror an existing fixture in
   the bucket to keep the values realistic.
3. Write `eval/test_set/expected/<bucket>_<short_name>.json` — the
   `{bucket, expected_overall, notes, application}` quartet.
4. Run `make eval`. Confirm the `verdict (actual)` row matches
   `verdict (expected)`. If they diverge, the fixture is mis-authored
   (likely) or the verifier has regressed (rare) — the harness will
   not "fix itself"; the fixture must be updated to match the verifier's
   real behavior, which by construction is what the regulation requires.
