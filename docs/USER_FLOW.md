# TTB Label Verification — User Flow

## Primary Flow

### Single-label verification (the default, Sarah / Dave path)

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. Land on /  — single-label form is the default screen          │
│    - Big drop target ("Drop label image here or click")          │
│    - All 7 expected-data fields visible, scrollable as one form  │
│    - "Try a sample label" button row near the top                │
│    - Beverage type select (Spirits / Wine / Malt / Other)        │
└────────────────────────┬─────────────────────────────────────────┘
                         │ drop / select image
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│ 2. Image preview renders (Alpine.js) — no upload yet             │
│    - Thumbnail with "remove" button                              │
│    - Form remains editable                                       │
└────────────────────────┬─────────────────────────────────────────┘
                         │ click "Verify"
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│ 3. HTMX hx-post → /verify   (loading indicator on the button)    │
│    Server-side:                                                  │
│     - Compute sha256(image_bytes)                                │
│     - Cache hit? Return cached LabelData (~1 ms)                 │
│     - Otherwise: call Gemini extractor (~1.5 s typical;          │
│       up to 12 s before timeout → OpenAI fallback)               │
│     - Run deterministic verifier (~10–50 ms)                     │
│     - Render _result_panel.html fragment                         │
└────────────────────────┬─────────────────────────────────────────┘
                         │ total ≤ 5 s typical, < 100 ms on cache hit
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│ 4. Result panel swaps into the page (hx-target)                  │
│    Layout, top to bottom:                                        │
│     - Image thumbnail (left) + colored verdict banner (right)    │
│       PASS green / WARN yellow / FAIL red / ERROR gray           │
│     - One-line summary ("All fields verified" /                  │
│       "ABV exceeds tolerance — see row 3" / etc.)                │
│     - Per-field table:                                           │
│         Field | Extracted | Expected | Verdict | Reason | CFR    │
│     - Collapsible "view raw extraction" panel (JSON for audit)   │
│     - "Verify another" button → resets form to step 1            │
└──────────────────────────────────────────────────────────────────┘
```

### "Try a sample label" demo flow (the reviewer's first click)

```
┌───────────────────────────────────────────────────────┐
│ Homepage shows 3 buttons:                             │
│  [Sample: Distilled Spirits — should PASS]            │
│  [Sample: ABV mismatch — should FAIL]                 │
│  [Sample: Malformed warning — FAIL (27 CFR 16.22)]    │
└───────────────────────────┬───────────────────────────┘
                            │ click one
                            ▼
                  GET /sample/{name}
                            │
                            ▼
┌───────────────────────────────────────────────────────┐
│ Form pre-filled with the sample's expected data       │
│ Image already attached (preview rendered)             │
│ "Verify" enabled; user clicks once                    │
└───────────────────────────┬───────────────────────────┘
                            │
                            ▼  same flow as steps 3–4 above
                  Result panel renders
```

### Batch verification flow (Jenny / Janet path)

```
┌───────────────────────────────────────────────────────────────────┐
│ 1. Navigate to /batch                                             │
│    - Big dropzone accepts N image files (Alpine.js)               │
│    - Optional CSV upload for expected data (one row per filename) │
│    - "Start batch" button shows count ("Verify 47 labels")        │
└─────────────────────────────┬─────────────────────────────────────┘
                              │ click "Start batch"
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│ 2. POST /batch with files + CSV → returns {run_id}                │
│    Browser opens SSE to /batch/stream/{run_id}                    │
└─────────────────────────────┬─────────────────────────────────────┘
                              │ asyncio.Semaphore(5) bounds concurrency
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│ 3. Results table populates row-by-row as each label completes     │
│    Each SSE "row" event = one _batch_row.html fragment            │
│    Each row shows: thumbnail | filename | verdict pill | one-line │
│       reason | "details" link → opens result panel inline         │
│    Filter chips above the table: All | Failures | Warnings | OK   │
│    Progress: "23 / 47 done — 2 ERROR / 4 FAIL / 1 WARN / 16 PASS" │
└─────────────────────────────┬─────────────────────────────────────┘
                              │ all complete
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│ 4. "Export CSV" button enabled                                    │
│    GET /batch/export/{run_id}.csv → downloadable summary          │
│    Columns: filename, verdict, failure_count, warning_count,      │
│      brand_pass, abv_pass, ..., raw_extraction_url                │
└───────────────────────────────────────────────────────────────────┘
```

### ERROR verdict flow (Jenny's bad-photo case)

```
Extractor returns a required field with confidence="low"
                │
                ▼
Verifier confidence gate → field verdict ERROR
                │
                ▼
Overall verdict → ERROR (worst-of)
                │
                ▼
Result panel renders with:
  - Gray ERROR banner ("Couldn't read this label reliably")
  - Per-field table shows which fields hit low confidence
  - Actionable reason ("class/type not legible — try a
    straighter angle, more light, or escalate to manual review")
  - NO PASS / FAIL claim — the tool refuses to verdict
```

## API Endpoints

### `POST /verify` — single-label form submission

**Request:** `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `image` | file (JPG / PNG) | yes | Max ~ 10 MB |
| `beverage_type` | string | yes | `distilled_spirits` \| `wine` \| `malt_beverage` \| `other` |
| `brand_name` | string | yes | |
| `class_type` | string | conditional | Required for spirits / wine |
| `alcohol_content_pct` | float | conditional | Required for spirits; optional for some wine / malt |
| `net_contents` | string | yes | e.g. `"750 mL"` |
| `bottler_name` | string | yes | |
| `bottler_address` | string | yes | |
| `is_import` | bool | yes | |
| `country_of_origin` | string | conditional | Required if `is_import=true` |

**Response:** `text/html` — `_result_panel.html` fragment.

**Error responses:**

- `400` — validation failure (missing required field per beverage type)
- `413` — image too large
- `415` — unsupported image type (only JPG / PNG)
- `502` — both extractors failed (Gemini + OpenAI fallback)

### `POST /verify/json` — single-label JSON path

**Request:** `multipart/form-data` with `image` file plus `expected` (JSON string of `ApplicationData`).

**Response:** same `_result_panel.html` fragment.

### `GET /sample/{name}` — pre-loaded sample

**Path values:** `spirits-pass` | `abv-fail` | `warning-fail`

**Response:** `text/html` — the homepage form pre-filled with the sample's expected data and image already attached.

### `POST /batch` — batch upload

**Request:** `multipart/form-data` with N image files plus an optional `expected_csv` file.

**Response:** `application/json` — `{"run_id": "..."}`.

### `GET /batch/stream/{run_id}` — SSE stream

**Response:** `text/event-stream`. Event types:

```
event: row
data: <div class="batch-row" ...>...</div>

event: progress
data: {"done": 23, "total": 47, "fail": 4, "warn": 1, "error": 2, "pass": 16}

event: done
data: {"run_id": "...", "duration_ms": 18420}
```

### `GET /batch/export/{run_id}.csv` — CSV export

**Response:** `text/csv` with one row per label and the summary columns described in the Batch flow diagram above.

### `GET /health` — liveness

**Response:** `{"status": "ok", "extractor": "gemini"}`.

## Example Queries

| Query | Expected Result | Expected Answer |
|---|---|---|
| `POST /verify` with sample `easy_distilled_spirits.jpg` and matching expected data | All 7 fields PASS | Overall: **PASS** (green banner). All field reasons read "matched". |
| `POST /verify` with spirits label, expected ABV 45.0 %, label shows 45.31 % | One field FAIL on ABV | Overall: **FAIL**. ABV reason: "ABV 45.31 % exceeds tolerance vs expected 45.0 % (delta 0.31 pp, tolerance ± 0.3 pp per 27 CFR 5.65(b))". |
| `POST /verify` with spirits label, expected brand "Stone's Throw", label shows "STONE'S THROW" | Brand PASS (silent) | Overall: **PASS**. Brand reason: "matched (normalized fuzzy 100)". |
| `POST /verify` with spirits label, expected bottler "Old Tom Distillery", label shows "Old Tom Distillery LLC" | Bottler PASS after suffix strip | Overall: **PASS**. Bottler reason: "matched (corporate-suffix stripped, fuzzy 100)". |
| `POST /verify` with label showing "Government Warning:" in title case | Warning text PASS, formatting FAIL | Overall: **FAIL**. Warning reason: "GOVERNMENT WARNING heading must be in all capital letters per 27 CFR 16.22 — extracted as title-case 'Government Warning'." |
| `POST /verify` with label showing "5.2% ABV" instead of "5.2% Alc./Vol." | ABV formatting FAIL | Overall: **FAIL**. ABV reason: "'ABV' abbreviation not permitted — use 'Alc./Vol.' or 'Alc. by Vol.' per 27 CFR 7.65(c)." |
| `POST /verify` twice with identical image bytes | Second call cache hit | Second call returns the same result panel; raw extraction shows `cache_hit: true`; latency < 100 ms. |
| `POST /verify` with a low-quality blurry photo | Class / type or warning at low confidence | Overall: **ERROR**. Reason: "could not reliably extract class/type — please reshoot at a different angle or escalate to manual review." |
| `POST /batch` with 47 labels, mix of PASS / FAIL | SSE stream of 47 rows | Progress events update the count; each row appears as its label completes; CSV export available when all done. |
| `POST /verify` with `EXTRACTOR_PROVIDER=openai` env override | Same flow, OpenAI used | Result panel `extractor_used: "openai"`; latency typically higher than Gemini but accuracy comparable; covered in the eval A / B. |
