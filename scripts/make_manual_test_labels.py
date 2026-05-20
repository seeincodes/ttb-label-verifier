"""Generate synthetic label image fixtures for manual end-to-end testing.

Two test suites are emitted under sample_data/manual_test/:

  Single-label suite (one image + one application.json each)
    pass/        — everything matches, expect overall PASS
    abv-fail/    — label 47% vs app 45.0% → FAIL with 27 CFR 5.65(b)
    warning-fail/ — warning missing "(2)" → FAIL with 27 CFR 16.21

  Batch suite (8 images + one expected.csv, for POST /batch)
    batch/<filename>.png × 8
    batch/expected.csv

  The batch set exercises all four beverage-type tolerance bands:
    - distilled spirits (27 CFR 5.65(b), ±0.3 pp)        ×4 labels
    - wine ≤14% ABV    (27 CFR 4.36,     ±1.5 pp)        ×1 label
    - wine >14% ABV    (27 CFR 4.36,     ±1.0 pp)        ×1 label
    - malt beverage    (27 CFR 7.65(c),  ±0.3 pp)        ×2 labels
  plus the 27 CFR 16.21 government-warning text rule.

Run once:
  PYTHONPATH=. .venv/bin/python scripts/make_manual_test_labels.py

This script is dev-only — Pillow is not in requirements.txt.
"""
from __future__ import annotations

import csv
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.verifier.warning import canonical_warning_text


WIDTH, HEIGHT = 900, 1200
BG = (244, 232, 200)
FG = (40, 28, 14)
ACCENT = (130, 30, 30)

FONT_DIR = "/System/Library/Fonts/Supplemental"
F_REG = f"{FONT_DIR}/Arial.ttf"
F_BOLD = f"{FONT_DIR}/Arial Bold.ttf"


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if font.getlength(trial) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_label(
    *,
    brand: str,
    class_type: str,
    abv_text: str,
    net_contents: str,
    bottler_block: str,
    warning: str,
) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)

    d.rectangle([(20, 20), (WIDTH - 20, HEIGHT - 20)], outline=ACCENT, width=4)
    d.rectangle([(40, 40), (WIDTH - 40, HEIGHT - 40)], outline=ACCENT, width=1)

    f_brand = _font(F_BOLD, 72)
    bw = f_brand.getlength(brand)
    d.text(((WIDTH - bw) / 2, 110), brand, fill=ACCENT, font=f_brand)

    f_class = _font(F_REG, 36)
    cw = f_class.getlength(class_type)
    d.text(((WIDTH - cw) / 2, 220), class_type, fill=FG, font=f_class)

    d.line([(120, 290), (WIDTH - 120, 290)], fill=ACCENT, width=2)

    f_big = _font(F_BOLD, 48)
    abv_w = f_big.getlength(abv_text)
    d.text(((WIDTH - abv_w) / 2, 360), abv_text, fill=FG, font=f_big)

    f_mid = _font(F_REG, 32)
    nw = f_mid.getlength(net_contents)
    d.text(((WIDTH - nw) / 2, 440), net_contents, fill=FG, font=f_mid)

    f_small = _font(F_REG, 22)
    y = 520
    for line in bottler_block.split("\n"):
        lw = f_small.getlength(line)
        d.text(((WIDTH - lw) / 2, y), line, fill=FG, font=f_small)
        y += 30

    f_warn = _font(F_REG, 18)
    f_warn_bold = _font(F_BOLD, 18)
    head, _, body = warning.partition(":")
    head = head + ":"
    body = body.strip()

    head_w = f_warn_bold.getlength(head + " ")
    max_w = WIDTH - 160
    first_line_max = max_w - head_w

    body_words = body.split()
    cur = ""
    while body_words:
        trial = f"{cur} {body_words[0]}".strip()
        if f_warn.getlength(trial) <= first_line_max:
            cur = trial
            body_words.pop(0)
        else:
            break
    first_line = cur
    remainder = " ".join(body_words)

    warn_y = HEIGHT - 280
    d.text((80, warn_y), head + " ", fill=FG, font=f_warn_bold)
    d.text((80 + head_w, warn_y), first_line, fill=FG, font=f_warn)

    for line in _wrap(remainder, f_warn, max_w):
        warn_y += 26
        d.text((80, warn_y), line, fill=FG, font=f_warn)

    return img


FOX_HOLLOW_BRAND_DISPLAY = "FOX HOLLOW"
FOX_HOLLOW_BRAND_APP = "Fox Hollow"  # application JSON uses title case → exercises fuzzy match
FOX_HOLLOW_CLASS = "Kentucky Straight Bourbon Whiskey"
NET_750 = "750 mL"
NET_355 = "355 mL"
ABV_45_TEXT = "45% ALC./VOL. (90 PROOF)"
ABV_47_TEXT = "47% ALC./VOL. (94 PROOF)"
FOX_HOLLOW_BOTTLER = "Fox Hollow Distillery LLC"
FOX_HOLLOW_ADDR = "47 Cooperage Ln, Bardstown, KY 40004"
FOX_HOLLOW_BOTTLER_BLOCK = f"BOTTLED BY {FOX_HOLLOW_BOTTLER}\n{FOX_HOLLOW_ADDR}"

CANONICAL = canonical_warning_text()
WARNING_MISSING_2 = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women "
    "should not drink alcoholic beverages during pregnancy because of "
    "the risk of birth defects."
)


def _spirits_app(*, brand: str, class_type: str, abv_pct: float,
                 net: str, bottler: str, addr: str) -> dict:
    return {
        "beverage_type": "distilled_spirits",
        "brand_name": brand,
        "class_type": class_type,
        "alcohol_content_pct": abv_pct,
        "net_contents": net,
        "bottler_name": bottler,
        "bottler_address": addr,
        "is_import": False,
    }


# ---------------------------------------------------------------------------
# Single-label suite (also used by the original three /verify smoke tests).
# ---------------------------------------------------------------------------

SAMPLES = [
    {
        "dir": "pass",
        "label_kwargs": {
            "brand": FOX_HOLLOW_BRAND_DISPLAY,
            "class_type": FOX_HOLLOW_CLASS,
            "abv_text": ABV_45_TEXT,
            "net_contents": NET_750,
            "bottler_block": FOX_HOLLOW_BOTTLER_BLOCK,
            "warning": CANONICAL,
        },
        "application": _spirits_app(
            brand=FOX_HOLLOW_BRAND_APP, class_type=FOX_HOLLOW_CLASS, abv_pct=45.0,
            net=NET_750, bottler=FOX_HOLLOW_BOTTLER, addr=FOX_HOLLOW_ADDR,
        ),
    },
    {
        "dir": "abv-fail",
        "label_kwargs": {
            "brand": FOX_HOLLOW_BRAND_DISPLAY,
            "class_type": FOX_HOLLOW_CLASS,
            "abv_text": ABV_47_TEXT,
            "net_contents": NET_750,
            "bottler_block": FOX_HOLLOW_BOTTLER_BLOCK,
            "warning": CANONICAL,
        },
        "application": _spirits_app(
            brand=FOX_HOLLOW_BRAND_APP, class_type=FOX_HOLLOW_CLASS, abv_pct=45.0,
            net=NET_750, bottler=FOX_HOLLOW_BOTTLER, addr=FOX_HOLLOW_ADDR,
        ),
    },
    {
        "dir": "warning-fail",
        "label_kwargs": {
            "brand": FOX_HOLLOW_BRAND_DISPLAY,
            "class_type": FOX_HOLLOW_CLASS,
            "abv_text": ABV_45_TEXT,
            "net_contents": NET_750,
            "bottler_block": FOX_HOLLOW_BOTTLER_BLOCK,
            "warning": WARNING_MISSING_2,
        },
        "application": _spirits_app(
            brand=FOX_HOLLOW_BRAND_APP, class_type=FOX_HOLLOW_CLASS, abv_pct=45.0,
            net=NET_750, bottler=FOX_HOLLOW_BOTTLER, addr=FOX_HOLLOW_ADDR,
        ),
    },
]


# ---------------------------------------------------------------------------
# Batch suite — 8 labels covering every tolerance band + the warning rule.
#
# Each entry produces one PNG and one row in expected.csv. Filename in the
# CSV must exactly match the filename uploaded to /batch.
# ---------------------------------------------------------------------------

# A batch row's `label_kwargs` are the visual / extracted side; `app` is what
# the agent declares (the CSV row). `expected_verdict` is purely documentation
# — what we expect the verifier to return — and feeds the README.
BATCH = [
    # 1. PASS — spirits, baseline (re-uses Fox Hollow so we can confirm
    # the cache: same image bytes hash → instant repeat in any later run)
    {
        "filename": "01_fox_hollow_bourbon.png",
        "expected_verdict": "PASS",
        "label_kwargs": {
            "brand": FOX_HOLLOW_BRAND_DISPLAY,
            "class_type": FOX_HOLLOW_CLASS,
            "abv_text": ABV_45_TEXT,
            "net_contents": NET_750,
            "bottler_block": FOX_HOLLOW_BOTTLER_BLOCK,
            "warning": CANONICAL,
        },
        "app": {
            "beverage_type": "distilled_spirits",
            "brand_name": FOX_HOLLOW_BRAND_APP,
            "class_type": FOX_HOLLOW_CLASS,
            "alcohol_content_pct": 45.0,
            "net_contents": NET_750,
            "bottler_name": FOX_HOLLOW_BOTTLER,
            "bottler_address": FOX_HOLLOW_ADDR,
            "is_import": False,
        },
    },
    # 2. PASS — spirits, gin
    {
        "filename": "02_emerald_isle_gin.png",
        "expected_verdict": "PASS",
        "label_kwargs": {
            "brand": "EMERALD ISLE",
            "class_type": "London Dry Gin",
            "abv_text": "40% ALC./VOL. (80 PROOF)",
            "net_contents": NET_750,
            "bottler_block": "BOTTLED BY Emerald Isle Distillers Ltd\n201 Juniper Way, Portland, OR 97214",
            "warning": CANONICAL,
        },
        "app": {
            "beverage_type": "distilled_spirits",
            "brand_name": "Emerald Isle",
            "class_type": "London Dry Gin",
            "alcohol_content_pct": 40.0,
            "net_contents": NET_750,
            "bottler_name": "Emerald Isle Distillers Ltd",
            "bottler_address": "201 Juniper Way, Portland, OR 97214",
            "is_import": False,
        },
    },
    # 3. PASS — spirits, tequila
    {
        "filename": "03_crescent_bay_tequila.png",
        "expected_verdict": "PASS",
        "label_kwargs": {
            "brand": "CRESCENT BAY",
            "class_type": "Silver Tequila",
            "abv_text": "38% ALC./VOL. (76 PROOF)",
            "net_contents": NET_750,
            "bottler_block": "IMPORTED BY Crescent Bay Spirits Co\n88 Coastal Hwy, San Diego, CA 92103",
            "warning": CANONICAL,
        },
        "app": {
            "beverage_type": "distilled_spirits",
            "brand_name": "Crescent Bay",
            "class_type": "Silver Tequila",
            "alcohol_content_pct": 38.0,
            "net_contents": NET_750,
            "bottler_name": "Crescent Bay Spirits Co",
            "bottler_address": "88 Coastal Hwy, San Diego, CA 92103",
            "is_import": True,
            "country_of_origin": "Mexico",
        },
    },
    # 4. FAIL — spirits, rum, 2 pp over 27 CFR 5.65(b)
    {
        "filename": "04_windward_rum_abv_fail.png",
        "expected_verdict": "FAIL (27 CFR 5.65(b))",
        "label_kwargs": {
            "brand": "WINDWARD",
            "class_type": "Aged Caribbean Rum",
            "abv_text": ABV_47_TEXT,
            "net_contents": NET_750,
            "bottler_block": "IMPORTED BY Windward Trading Co\n14 Harbor Pl, Miami, FL 33132",
            "warning": CANONICAL,
        },
        "app": {
            "beverage_type": "distilled_spirits",
            "brand_name": "Windward",
            "class_type": "Aged Caribbean Rum",
            "alcohol_content_pct": 45.0,  # label says 47% → 2.0 pp delta
            "net_contents": NET_750,
            "bottler_name": "Windward Trading Co",
            "bottler_address": "14 Harbor Pl, Miami, FL 33132",
            "is_import": True,
            "country_of_origin": "Jamaica",
        },
    },
    # 5. PASS — wine ≤14% (1.0 pp inside the ±1.5 pp band, 27 CFR 4.36)
    {
        "filename": "05_valley_springs_cab.png",
        "expected_verdict": "PASS",
        "label_kwargs": {
            "brand": "VALLEY SPRINGS",
            "class_type": "Napa Valley Cabernet Sauvignon",
            "abv_text": "13.5% ALC./VOL.",
            "net_contents": NET_750,
            "bottler_block": "BOTTLED BY Valley Springs Winery\n3120 Vine Trail, St. Helena, CA 94574",
            "warning": CANONICAL,
        },
        "app": {
            "beverage_type": "wine",
            "brand_name": "Valley Springs",
            "class_type": "Napa Valley Cabernet Sauvignon",
            "alcohol_content_pct": 12.5,  # 1.0 pp delta, within ±1.5 pp (≤14% band)
            "net_contents": NET_750,
            "bottler_name": "Valley Springs Winery",
            "bottler_address": "3120 Vine Trail, St. Helena, CA 94574",
            "is_import": False,
        },
    },
    # 6. FAIL — wine >14% (1.5 pp over the ±1.0 pp band, 27 CFR 4.36 — proves
    # the high-side band; a backwards implementation would silently PASS)
    {
        "filename": "06_old_harbor_zin_wine_band.png",
        "expected_verdict": "FAIL (27 CFR 4.36)",
        "label_kwargs": {
            "brand": "OLD HARBOR",
            "class_type": "California Zinfandel",
            "abv_text": "15.5% ALC./VOL.",
            "net_contents": NET_750,
            "bottler_block": "BOTTLED BY Old Harbor Vineyards\n900 Estate Rd, Paso Robles, CA 93446",
            "warning": CANONICAL,
        },
        "app": {
            "beverage_type": "wine",
            "brand_name": "Old Harbor",
            "class_type": "California Zinfandel",
            "alcohol_content_pct": 14.0,  # label 15.5% → 1.5 pp over the ±1.0 pp band (>14%)
            "net_contents": NET_750,
            "bottler_name": "Old Harbor Vineyards",
            "bottler_address": "900 Estate Rd, Paso Robles, CA 93446",
            "is_import": False,
        },
    },
    # 7. PASS — malt beverage at edge of ±0.3 pp (27 CFR 7.65(c))
    {
        "filename": "07_northern_peak_ipa.png",
        "expected_verdict": "PASS",
        "label_kwargs": {
            "brand": "NORTHERN PEAK",
            "class_type": "India Pale Ale",
            "abv_text": "6.8% ALC./VOL.",
            "net_contents": NET_355,
            "bottler_block": "BREWED BY Northern Peak Brewing\n412 Summit St, Burlington, VT 05401",
            "warning": CANONICAL,
        },
        "app": {
            "beverage_type": "malt_beverage",
            "brand_name": "Northern Peak",
            "class_type": "India Pale Ale",
            "alcohol_content_pct": 6.5,  # label 6.8 → 0.3 pp, at the band edge
            "net_contents": NET_355,
            "bottler_name": "Northern Peak Brewing",
            "bottler_address": "412 Summit St, Burlington, VT 05401",
            "is_import": False,
        },
    },
    # 8. FAIL — malt beverage, warning text missing "(2)" sentence (27 CFR 16.21)
    {
        "filename": "08_rustic_field_stout_warning_fail.png",
        "expected_verdict": "FAIL (27 CFR 16.21)",
        "label_kwargs": {
            "brand": "RUSTIC FIELD",
            "class_type": "Stout",
            "abv_text": "5.5% ALC./VOL.",
            "net_contents": NET_355,
            "bottler_block": "BREWED BY Rustic Field Brewery\n75 Mill Rd, Asheville, NC 28801",
            "warning": WARNING_MISSING_2,
        },
        "app": {
            "beverage_type": "malt_beverage",
            "brand_name": "Rustic Field",
            "class_type": "Stout",
            "alcohol_content_pct": 5.5,
            "net_contents": NET_355,
            "bottler_name": "Rustic Field Brewery",
            "bottler_address": "75 Mill Rd, Asheville, NC 28801",
            "is_import": False,
        },
    },
]

# Column order matches `parse_expected_csv` (app/batch.py:111). `filename`
# must be the first column and exactly match each PNG name in batch/.
CSV_COLUMNS = [
    "filename", "beverage_type", "brand_name", "class_type",
    "alcohol_content_pct", "net_contents", "bottler_name", "bottler_address",
    "is_import", "country_of_origin",
]


def _csv_cell(value: object) -> str:
    """Render an ApplicationData value as the CSV string parse_expected_csv expects."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_batch_csv(out_dir: Path) -> Path:
    """Emit expected.csv in the column order parse_expected_csv reads."""
    csv_path = out_dir / "expected.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for entry in BATCH:
            row = dict.fromkeys(CSV_COLUMNS, "")
            row["filename"] = entry["filename"]
            for k, v in entry["app"].items():
                if v is None:
                    continue
                row[k] = _csv_cell(v)
            writer.writerow(row)
    return csv_path


def main() -> None:
    out_root = Path(__file__).resolve().parent.parent / "sample_data" / "manual_test"
    out_root.mkdir(parents=True, exist_ok=True)

    # Single-label suite
    for spec in SAMPLES:
        sub = out_root / spec["dir"]
        sub.mkdir(exist_ok=True)
        img = _draw_label(**spec["label_kwargs"])
        img.save(sub / "label.png", optimize=True)
        (sub / "application.json").write_text(
            json.dumps(spec["application"], indent=2) + "\n"
        )
        print(f"wrote {sub}/label.png + application.json")

    # Batch suite
    batch_dir = out_root / "batch"
    batch_dir.mkdir(exist_ok=True)
    for entry in BATCH:
        img = _draw_label(**entry["label_kwargs"])
        img.save(batch_dir / entry["filename"], optimize=True)
        print(f"wrote {batch_dir / entry['filename']}")
    csv_path = _write_batch_csv(batch_dir)
    print(f"wrote {csv_path}")

    readme = textwrap.dedent(
        """\
        # Manual-test labels (synthetic, end-to-end)

        Generated by `scripts/make_manual_test_labels.py` to exercise the live
        Gemini → verifier path. Unlike `sample_data/*.json` (which bypass the
        extractor), these are intended for the real upload endpoints so the
        vision model is actually called.

        ## Single-label suite — POST /verify or /verify/json

        | Folder         | Expected overall | Why                                                          |
        | -------------- | ---------------- | ------------------------------------------------------------ |
        | `pass/`        | PASS             | Everything on the label matches the application JSON.        |
        | `abv-fail/`    | FAIL             | Label declares 47% ABV; application says 45.0% (2.0 pp ≫ 0.3 pp tolerance, 27 CFR 5.65(b)). |
        | `warning-fail/`| FAIL             | Government warning omits the "(2)" sentence (27 CFR 16.21).  |

        ```bash
        curl -sS -X POST http://localhost:8000/verify/json \\
          -F image=@sample_data/manual_test/pass/label.png \\
          -F application_json="$(cat sample_data/manual_test/pass/application.json)"
        ```

        ## Batch suite — POST /batch (8 labels + one CSV)

        Eight labels in `batch/` covering every tolerance band the verifier
        knows about, plus the 27 CFR 16.21 warning-text rule:

        | # | File                                       | Beverage      | Expected   | What it exercises                                |
        | - | ------------------------------------------ | ------------- | ---------- | ------------------------------------------------ |
        | 1 | 01_fox_hollow_bourbon.png                  | spirits       | PASS       | baseline (re-used → cache hit on repeat runs)    |
        | 2 | 02_emerald_isle_gin.png                    | spirits       | PASS       | different brand + class                          |
        | 3 | 03_crescent_bay_tequila.png                | spirits       | PASS       | import path (`is_import=true`, country)          |
        | 4 | 04_windward_rum_abv_fail.png               | spirits       | FAIL       | ABV 47 vs 45 → 27 CFR 5.65(b), ±0.3 pp           |
        | 5 | 05_valley_springs_cab.png                  | wine ≤14%     | PASS       | wine band ±1.5 pp (27 CFR 4.36)                  |
        | 6 | 06_old_harbor_zin_wine_band.png            | wine >14%     | FAIL       | wine band ±1.0 pp (27 CFR 4.36, high side)       |
        | 7 | 07_northern_peak_ipa.png                   | malt          | PASS       | malt ±0.3 pp at band edge (27 CFR 7.65(c))       |
        | 8 | 08_rustic_field_stout_warning_fail.png     | malt          | FAIL       | warning missing "(2)" → 27 CFR 16.21             |

        Browser flow: open <http://localhost:8000/batch>, drop all 8 PNGs from
        `sample_data/manual_test/batch/` into the file picker, paste the
        contents of `batch/expected.csv` into the expected-CSV box, hit upload.
        Watch the SSE stream fill in row-by-row.

        Or via curl (no SSE, just kick off the run and grab the export):

        ```bash
        RUN_JSON=$(curl -sS -X POST http://localhost:8000/batch \\
          $(for f in sample_data/manual_test/batch/*.png; do printf -- '-F files=@%s ' "$f"; done) \\
          -F "expected_csv=$(cat sample_data/manual_test/batch/expected.csv)")
        RUN_ID=$(echo "$RUN_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
        curl -sN http://localhost:8000/batch/stream/$RUN_ID  # streams until done
        curl -s   http://localhost:8000/batch/export/$RUN_ID.csv > /tmp/batch_results.csv
        ```
        """
    )
    (out_root / "README.md").write_text(readme)
    print(f"wrote {out_root}/README.md")


if __name__ == "__main__":
    main()
