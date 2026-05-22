# TTB Label Verification Prototype — Planning Document (v2)

> **Purpose:** This is the spec an AI coding agent (Claude Code, Cursor, etc.) will work from to build the deliverable. It captures all decisions, trade-offs, and stakeholder context up front so the build phase is execution, not re-discovery.

> **v2 changes:** Stack collapsed to single Python service (FastAPI + HTMX + Tailwind, no separate frontend repo). Verdict taxonomy and match-flavor handling fully specified. TTB regulatory citations integrated throughout. Government warning text and ABV tolerances sourced from 27 CFR directly. Beverage-type conditionality made explicit.

---

## 1. Context

**Role being interviewed for:** AI Engineer / IT Specialist, US Department of the Treasury (TTB falls under Treasury).
**Time budget:** ~15–25 hours, ~1 week part-time.
**Deliverable:** Source code repo + deployed working prototype + README.

**What we're building:** A prototype tool that helps TTB compliance agents verify that an alcohol label image matches the data submitted in its application. Agent uploads (a) the label image and (b) the expected application data; the tool extracts label fields via vision AI, compares them to expected values using deterministic Python rules grounded in 27 CFR, and returns a structured verdict with explainable reasoning.

**Why this matters to the reviewer:** This is for a federal AI engineering role at Treasury. The evaluators will weigh AI judgment (prompt design, when to use AI vs. deterministic code, eval thinking, handling ambiguity, regulatory grounding) at least as heavily as backend craft. The writeup matters as much as the code. Citing 27 CFR sections inline shows you actually read the regs, which is a strong signal for a Treasury role.

---

## 2. Stakeholder signals — what to optimize for

From the discovery interviews, the following are explicit requirements that must be honored or addressed in the writeup. Missing these = missing the point of the exercise.

| Signal | Source | How we address |
|---|---|---|
| **≤5s response per label** | Sarah Chen — "If we can't get results back in about 5 seconds, nobody's going to use it." | Default model = Gemini 2.5 Flash (~1.5s avg). In-memory LRU cache keyed by image hash. SSE streaming for batch so the user sees progress, not a spinner. |
| **"My 73-year-old mother could figure it out"** | Sarah Chen | One primary action per screen. Large hit targets. No nested menus. Plain English. Default state visible without scrolling. Pre-loaded "Try a sample label" button. |
| **Batch upload (200–300 labels)** | Sarah Chen — Janet from Seattle | First-class feature. SSE streaming so each item appears in the results table as it completes. Concurrency-limited parallelism (5 concurrent). CSV export. |
| **Fuzzy matching nuance** | Dave Morrison — "STONE'S THROW" vs "Stone's Throw" must match | Normalize (lowercase, strip punctuation, collapse whitespace) + `rapidfuzz` token-sort ratio. ≥95 = silent PASS. 80–94 = WARN. <80 = FAIL. |
| **Government warning verbatim + formatting** | Jenny Park | Two-layer check: (1) text-content exact match against canonical TTB text per 27 CFR 16.21, (2) vision-model formatting question per 27 CFR 16.22. Both must pass. |
| **Bad photos: angles, glare, low light** | Jenny Park | Vision model handles this natively. Per-field confidence in extraction prompt — low confidence → ERROR verdict with actionable feedback, never a false PASS/FAIL. |
| **Network/firewall constraints** | Marcus Williams | Stateless API calls to one vendor endpoint. Document exact domains needing allowlisting in README (generativelanguage.googleapis.com for Gemini, api.openai.com for fallback). |
| **Mixed tech comfort (Dave vs. Jenny)** | Sarah Chen | Default flow is dead-simple for Dave; keyboard shortcuts + bulk ops for Jenny. |
| **PII / federal compliance for prod** | Marcus Williams | Out of scope for prototype, acknowledged in README "Production considerations" section. No data persistence by default. |

---

## 3. Architecture decisions

### 3.1 Approach: Hybrid (vision AI for extraction, deterministic code for verification)

```
┌─────────────────┐    ┌──────────────────┐    ┌──────────────────────┐
│ Label image +   │───▶│ Vision model     │───▶│ Structured JSON      │
│ expected fields │    │ (Gemini/OpenAI)  │    │ {field: {value,      │
│   (form or JSON)│    │ via abstraction  │    │  confidence}}        │
└─────────────────┘    └──────────────────┘    └──────────┬───────────┘
                                                          │
                                                          ▼
                       ┌──────────────────────────────────────────┐
                       │ Deterministic verifier (Python)          │
                       │  - Normalize (case, punctuation, units)  │
                       │  - Fuzzy match (rapidfuzz token-sort)    │
                       │  - Numeric tolerance per 27 CFR          │
                       │    (spirits 5.65, malt 7.65, wine 4.36)  │
                       │  - Regex exact match (gov warning text)  │
                       │  - Formatting rules (27 CFR 16.22)       │
                       │  - Per-field confidence gate             │
                       └──────────────────┬───────────────────────┘
                                          │
                                          ▼
                       ┌──────────────────────────────────────────┐
                       │ Verdict per field + overall              │
                       │ PASS | WARN | FAIL | ERROR               │
                       │ + reasoning + CFR citation + margin      │
                       │ + raw model response (for audit)         │
                       └──────────────────────────────────────────┘
```

**Why hybrid, not pure-LLM:** Federal context demands explainability and auditability. An LLM saying "this label fails" is unacceptable for a compliance decision; a Python rule saying "ABV on label (45.31%) exceeds expected (45.0%) by 0.31pp; tolerance per 27 CFR 5.65 is ±0.3pp" is reviewable, citable, and reproducible. Use AI where it shines (reading messy images), use deterministic code where it shines (regulatory rule enforcement).

**Writeup angle (`docs/DESIGN_NOTES.md §1`):** *"Why I didn't just throw everything at the LLM"* — covered explicitly there. (Originally landed in the README as §5; moved out in the 2026-05-22 README trim, content preserved verbatim.)

### 3.2 Model selection: Gemini primary, OpenAI fallback

**Default:** Gemini 2.5 Flash for the extraction call.
- ~1.5s average latency — only model that comfortably fits the 5s bar
- ~$1.67 per 10,000 pages — defensible federal cost story
- Available on GSA MAS (added Aug 2025); Vertex AI has FedRAMP/IL4 path
- Accuracy gap vs. Claude is small for short, mostly-printed label text

**Fallback:** OpenAI GPT-4o via the same `LabelExtractor` interface.
- Strongest pure OCR on degraded images
- Available on GSA MAS; Azure OpenAI has FedRAMP High
- Used (a) as automatic fallback if Gemini call fails or times out, (b) as A/B comparison in eval suite

**Architecture:** `LabelExtractor` abstract base class (Python ABC, ~30 lines) with two concrete implementations. Swap default via `EXTRACTOR_PROVIDER` env var. README discusses why this matters for federal procurement (no vendor lock-in; an agency may have only one approved provider on their MAS).

**Explicitly NOT using:** LiteLLM or similar wrapper libraries. Rolling our own abstraction is ~30 lines, shows architectural taste, and the writeup ("here's how I'd add a Bedrock implementation for an agency on AWS GovCloud") is more impressive.

### 3.3 Stack — single Python service

| Layer | Choice | Reason |
|---|---|---|
| Web framework | FastAPI | Idiomatic Python, async-native for batch, auto OpenAPI docs |
| Templates | Jinja2 (FastAPI default) | Standard, well-known, fits the HTMX HTML-fragment pattern perfectly |
| Frontend interactivity | HTMX (~14KB) | `hx-post` / `hx-target` for form-driven UI; `hx-ext="sse"` for batch live updates. No JS framework, no build step, no separate repo. |
| Visual styling | Tailwind CSS via Play CDN | One `<script>` tag, zero toolchain. Production would use the CLI build (documented in README). |
| Client-side state (minimal) | Alpine.js (~5KB) | For drag-and-drop multi-file dropzone in batch UI, image preview, and small toggles. ~30 lines of code total. |
| Vision models | Gemini 2.5 Flash (primary), GPT-4o (fallback) | See 3.2 |
| Matching | `rapidfuzz` (fuzzy strings), stdlib `re` (warning regex), `pydantic` (schema validation) | Battle-tested, deterministic, fast |
| Config | Pydantic Settings reading from `.env` | Type-safe; `.env.example` doubles as config documentation |
| Caching | `functools.lru_cache` (or `cachetools.LRUCache` if we need TTL) | In-memory, keyed by SHA-256 of image bytes. ~10 lines. |
| Hosting | Render (single web service) or Fly.io | Single-service deploy. No CORS, no two-service plumbing. |
| Storage | None by default (in-memory only) | Avoids PII issues; explicitly noted as a prototype constraint |
| Eval | Custom Python eval harness + ~20 test labels | Demonstrates eval thinking — critical for AI/SE role |
| Testing | Pytest for verifier rules only | Unit-test the deterministic Python; eval suite is the integration test (run manually) |

### 3.4 Repo structure

Flat monorepo, not `src/` library layout:

```
ttb-label-verifier/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, routes
│   ├── extractors/
│   │   ├── base.py          # LabelExtractor ABC
│   │   ├── gemini.py        # GeminiExtractor
│   │   └── openai.py        # OpenAIExtractor
│   ├── verifier/
│   │   ├── __init__.py
│   │   ├── rules.py         # Per-field verification rules (cite CFR in docstrings)
│   │   ├── normalize.py     # Text/unit normalization
│   │   ├── tolerances.py    # Per-beverage ABV tolerance lookup
│   │   └── warning.py       # Gov warning canonical text + formatting check
│   ├── models.py            # Pydantic schemas (LabelData, VerificationResult, etc.)
│   ├── cache.py             # LRU cache for extraction results
│   ├── config.py            # Pydantic Settings
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html       # Single-label flow
│   │   ├── batch.html       # Batch flow
│   │   ├── _result_panel.html      # HTMX-swapped fragment
│   │   └── _batch_row.html         # SSE-streamed fragment
│   └── static/
│       └── (Tailwind via CDN, htmx via CDN, minimal custom CSS if any)
├── tests/
│   ├── test_normalize.py
│   ├── test_rules.py
│   ├── test_tolerances.py
│   └── test_warning.py
├── eval/
│   ├── harness.py           # Run eval suite, print metrics
│   ├── test_set/
│   │   ├── labels/          # Image files (~20)
│   │   └── expected/        # JSON files, one per label
│   └── results/             # Eval run outputs (gitignored)
├── sample_data/
│   ├── easy_distilled_spirits.jpg
│   ├── easy_wine.jpg
│   └── easy_malt.jpg
├── .env.example
├── .gitignore
├── Dockerfile
├── Makefile                 # make dev / make eval / make test / make deploy
├── pyproject.toml
├── README.md
└── requirements.txt
```

---

## 4. Scope — what we build

### 4.1 Core features (must-have)

1. **Single-label verification flow**
   - Upload a label image (JPG / PNG); PDF support documented as future work
   - Provide expected application data via *either*:
     - Web form with all 7+ fields
     - Structured JSON upload (Pydantic schema in §5.2)
   - Image preview shown before submit
   - One-click "Verify"
   - Result panel: image + colored verdict banner + per-field table with extracted/expected/verdict/reasoning/CFR citation

2. **TTB checklist coverage** (all fields, per brief)
   - Brand name (fuzzy match)
   - Class/type designation (fuzzy match, beverage-type-aware)
   - Alcohol content / ABV (numeric with per-beverage tolerance; format check)
   - Net contents (unit-normalized)
   - Name and address of bottler/producer (fuzzy, strip corporate suffixes for matching)
   - Country of origin (exact match, only if `is_import = true`)
   - Government Health Warning Statement (canonical text + formatting check per 27 CFR 16.21–16.22)

3. **Batch upload with live progress**
   - Drag-and-drop multiple files (Alpine.js dropzone)
   - Optional CSV of expected data, one row per filename
   - SSE-streamed results: each row appears in the results table as it finishes
   - Concurrency limit of 5 simultaneous extractions
   - Filter (failures only / warnings only / all)
   - CSV export of results

4. **Explainability**
   - Each verdict shows: extracted value, expected value, comparison method used, confidence, evidence snippet, **CFR citation where applicable**
   - For warning failures: distinguish "text mismatch" from "formatting violation" with the specific reg cited
   - Raw model response included in the result payload (collapsible "view raw extraction" panel) for audit

5. **Sample data pre-loaded for demo**
   - "Try a sample label" button on the homepage so a reviewer can click once and see the flow without uploading
   - 3 samples: a clean distilled spirits label that PASSES, one with an ABV mismatch that FAILS, one with a malformed warning that FAILS with a formatting-specific reason

### 4.2 Stretch features (if time)

- Image-quality pre-check with actionable feedback ("photo is too dark — please reshoot")
- Side-by-side image viewer with bounding-box overlays on detected fields
- A/B model comparison (Gemini + OpenAI side by side on the same label)
- Keyboard shortcuts for power users (Jenny's case)
- Eval dashboard accessible from the running app (not just CLI)
- Wine class-boundary edge case (a 14.5% wine labeled "table wine" — class designation problem even if numeric tolerance passes)

### 4.3 Out of scope (documented in README)

- COLA integration (per Marcus)
- Authentication / user management
- Persistent storage of labels (PII concerns)
- Production-grade observability (logging beyond prototype level)
- Multi-page PDF handling (single image only)
- Background job queue for batches over ~50 labels (writeup explains the production path)

---

## 5. Detailed specifications

### 5.1 Government warning — canonical text (from 27 CFR 16.21)

Required text, exact:

> **GOVERNMENT WARNING:** (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems.

Formatting requirements (per 27 CFR 16.22):
- "GOVERNMENT WARNING" must be in **capital letters AND bold type**
- Remainder must NOT be bold
- Must appear as a continuous statement
- Must appear separate and apart from other information
- Minimum font size depends on container volume:
  - ≥ 1 mm for containers ≤ 237 ml (8 fl. oz.)
  - ≥ 2 mm for containers > 237 ml and ≤ 3 L
  - ≥ 3 mm for containers > 3 L

Verification approach:
1. **Text content check** — strip all formatting from extracted warning, normalize whitespace, compare to canonical (case-insensitive for content). Must match exactly minus whitespace differences.
2. **Formatting check** — vision model prompted: "Looking only at the government warning on this label, answer three yes/no questions: (a) Is the phrase 'GOVERNMENT WARNING' rendered in ALL CAPITAL LETTERS? (b) Is 'GOVERNMENT WARNING' rendered in bold/heavier weight than the rest of the warning? (c) Does the warning appear as a continuous statement, not broken up by other content?"
3. Both must pass for warning to verify. If text matches but formatting fails, the verdict cites 27 CFR 16.22 specifically.

### 5.2 Expected application data — Pydantic schema

```python
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class BeverageType(str, Enum):
    DISTILLED_SPIRITS = "distilled_spirits"  # 27 CFR Part 5
    WINE = "wine"                             # 27 CFR Part 4
    MALT_BEVERAGE = "malt_beverage"           # 27 CFR Part 7
    OTHER = "other"                           # seltzers, RTDs, ciders ≥7% — universal fields only

class ApplicationData(BaseModel):
    beverage_type: BeverageType
    brand_name: str
    class_type: Optional[str] = None          # required for spirits & wine; optional for malt
    alcohol_content_pct: Optional[float] = None  # optional for some wine/beer per TTB
    net_contents: str                         # e.g. "750 mL"
    bottler_name: str
    bottler_address: str
    is_import: bool = False
    country_of_origin: Optional[str] = None   # required if is_import
```

### 5.3 ABV tolerances by beverage type (from 27 CFR)

| Beverage | Tolerance | Notes | Citation |
|---|---|---|---|
| Distilled spirits | ±0.3 pp | | 27 CFR 5.65(b) |
| Malt beverages | ±0.3 pp | But labeled ≥0.5% may not actually be <0.5%; "low alcohol" cap at 2.5% | 27 CFR 7.65(c) |
| Wine, ≤14% ABV | ±1.5 pp | Cannot cross class boundary (table wine caps at 14%) | 27 CFR 4.36 |
| Wine, >14% ABV | ±1.0 pp | | 27 CFR 4.36 |

Additional formatting rules to check (all three categories):
- The abbreviation "ABV" is **NOT permitted** on the label. Acceptable: "Alc. by Vol.", "Alc./Vol.", "ALC. BY VOL.", with or without periods, % symbol allowed. Violation → FAIL with citation to 27 CFR 5.65 / 7.65 / 4.36 as appropriate.

### 5.4 Verdict taxonomy and match flavors

Verdict values:
- **PASS** — required fields present, all match within tolerance, no formatting violations
- **WARN** — extracted but borderline (fuzzy score 80–94, ABV within 2× tolerance, image-quality marginal). Needs human review; never auto-accepts.
- **FAIL** — clear mismatch, missing required field, regulatory formatting violation, or value outside tolerance
- **ERROR** — could not extract reliably (image unreadable, model timeout, low-confidence on a required field)

Overall verdict = worst across all checked fields (ERROR > FAIL > WARN > PASS).

Match flavors and how each resolves:

| Flavor | Example | Verdict | Mechanism |
|---|---|---|---|
| Cosmetic difference | `STONE'S THROW` vs `Stone's Throw` | PASS (silent) | Normalize + fuzzy ≥95 |
| Borderline match | `Old Tom Distillery LLC` vs `Old Tom Distillery` | WARN | Fuzzy 80–94, agent review surfaced |
| Numeric within tolerance | `45.2%` vs `45.0%` ABV (spirits, ±0.3pp) | PASS (silent) | Per-beverage tolerance lookup |
| Equivalent representation | `750 mL` vs `0.75 L` | PASS (silent) | Unit normalization |
| Warning formatting violation | "Government Warning" (title case) | FAIL | Two-layer warning check; cites 27 CFR 16.22 |
| Just over tolerance | `45.31%` vs `45.0%` ABV | FAIL (with margin + reg cite) | Tolerance check returns margin; FAIL message includes pp delta and CFR section |
| Low extraction confidence | Blurry image, model uncertain | ERROR (actionable) | Per-field confidence from model; required field at low confidence → ERROR |
| Disallowed abbreviation | `5.2% ABV` (per regs must be "alc./vol.") | FAIL (formatting violation) | String check on extracted abbreviation |

### 5.5 Per-field confidence in extraction prompt

The extraction prompt must instruct the model to return per-field structured output of the form:

```json
{
  "brand_name": {"value": "OLD TOM DISTILLERY", "confidence": "high"},
  "class_type": {"value": "Kentucky Straight Bourbon Whiskey", "confidence": "high"},
  "alcohol_content_pct": {"value": 45.0, "confidence": "high"},
  "alcohol_content_text": {"value": "45% ALC./VOL. (90 PROOF)", "confidence": "high"},
  "net_contents": {"value": "750 mL", "confidence": "high"},
  "bottler_name": {"value": "...", "confidence": "medium"},
  "bottler_address": {"value": "...", "confidence": "low"},
  "country_of_origin": {"value": null, "confidence": "high"},
  "government_warning_text": {"value": "GOVERNMENT WARNING: ...", "confidence": "high"},
  "government_warning_formatting": {
    "caps_correct": true,
    "bold_correct": true,
    "continuous": true,
    "confidence": "high"
  }
}
```

Confidence values: `high | medium | low`. Prompt instructs the model to return `null` and `low` confidence rather than guess. The verifier treats any required field with `low` confidence as an ERROR (cannot reliably verify; human review required).

### 5.6 Beverage-type conditionality

| Field | Spirits | Wine | Malt | Other |
|---|---|---|---|---|
| Brand name | Required | Required | Required | Required |
| Class/type | Required | Required | Optional | Optional |
| ABV | Required (with format/tolerance per 5.65) | Conditional (varies by type) | Conditional (≥0.5%) | Required if present on label |
| Net contents | Required | Required | Required | Required |
| Bottler name/address | Required | Required | Required | Required |
| Country of origin | If import | If import | If import | If import |
| Gov warning | Required (per 27 CFR 16) | Required (per 27 CFR 16) | Required (per 27 CFR 16) | Required (per 27 CFR 16) |

When `beverage_type = OTHER`, the verifier verifies only the universally required fields (brand, net contents, bottler, warning) and skips type-specific class/type rules. The README documents this as an extensibility point.

### 5.7 CFR citation strategy

Every verifier rule has a docstring citing the relevant section. Every FAIL or WARN verdict that has a regulatory basis includes the citation in its reasoning string. Example:

```python
def check_abv_tolerance(extracted: float, expected: float, beverage: BeverageType) -> Verdict:
    """Verify ABV against expected value within TTB tolerance.

    Tolerances per CFR:
    - Distilled spirits: ±0.3 pp (27 CFR 5.65(b))
    - Malt beverages: ±0.3 pp (27 CFR 7.65(c))
    - Wine ≤14% ABV: ±1.5 pp (27 CFR 4.36)
    - Wine >14% ABV: ±1.0 pp (27 CFR 4.36)
    """
    tol = tolerance_for(beverage, expected)
    delta = abs(extracted - expected)
    if delta <= tol:
        return Verdict.PASS
    if delta <= 2 * tol:
        return Verdict.WARN.with_reason(
            f"ABV {extracted}% differs from expected {expected}% by {delta:.2f}pp "
            f"(tolerance ±{tol}pp per 27 CFR {cfr_for(beverage)})"
        )
    return Verdict.FAIL.with_reason(
        f"ABV {extracted}% exceeds tolerance vs expected {expected}% "
        f"(delta {delta:.2f}pp, tolerance ±{tol}pp per 27 CFR {cfr_for(beverage)})"
    )
```

[`docs/DESIGN_NOTES.md §2`](DESIGN_NOTES.md) has a table of all CFR sections referenced. (Originally lived in the README as §6.)

### 5.8 Caching

In-memory LRU cache (`functools.lru_cache` with `maxsize=128`, or `cachetools.LRUCache` if TTL is needed).

Cache key: `sha256(image_bytes)`. Cache value: the full extraction JSON.

Cache hit returns in ~1ms instead of ~1.5s. Demoable: re-verify the same label and it's instant.

README documents this is in-memory only (lost on restart); production would use Redis or a content-addressed store.

---

## 6. Eval strategy

This is where you signal "AI engineer" not "vibes engineer."

### 6.1 Test set (~20 labels)

Mix:
- **5 easy cases** — clean, well-lit, all fields correct for each beverage type (distilled spirits, wine, malt beverage, plus 1-2 "other")
- **5 hard image quality** — angle, glare, low light, partial occlusion (Jenny's pain points)
- **5 violations** — wrong ABV, missing warning, malformed warning (title case), disallowed "ABV" abbreviation, brand mismatch
- **5 edge cases** — Dave's "STONE'S THROW" vs "Stone's Throw"; `45% Alc./Vol.` vs `45.0% ABV`; LLC suffix difference; 750 mL vs 0.75 L; just-over-tolerance numeric

Generate test labels using AI image generation (as the brief suggests). Document the generation prompts in `eval/test_set/GENERATION.md` for reproducibility. Cite any non-generated images.

### 6.2 Metrics tracked

- Per-field extraction accuracy
- False positive rate (incorrectly PASSes a violation)
- False negative rate (incorrectly FAILs a valid label) — worst case for compliance
- Verdict distribution by category (does WARN actually fire when it should?)
- p50 / p95 / p99 latency
- Cost per label
- Cache hit rate on second-pass

### 6.3 Eval harness

- Runnable via `make eval`
- Prints a summary table to console and writes JSON results to `eval/results/`
- The README "Eval results" section includes the actual numbers from the final run with a frank discussion of failure modes (originally landed as README §9; renamed in the 2026-05-22 trim)
- Comparison run: same eval with `EXTRACTOR_PROVIDER=openai` to show the model-swap works and to surface accuracy/cost trade-offs

---

## 7. Build plan — time-boxed phases

Total: ~21 hours, with ~2hr buffer.

| Phase | Hours | Output |
|---|---|---|
| **0. Setup** | 1 | Repo scaffold, FastAPI hello, Jinja2 + HTMX + Tailwind base template, Gemini API smoke test, OpenAI API smoke test, deploy skeleton on Render |
| **1. Core extractor** | 3 | `LabelExtractor` ABC + Gemini implementation. Per-field-confidence extraction prompt with structured JSON output. Manual testing on 3-4 sample labels. |
| **2. Verifier (rules + normalization + tolerances)** | 4 | Per-field rules with CFR-cited docstrings. Normalization layer. Tolerance lookup. Warning text + formatting check. Pydantic models. Unit tests. |
| **3. Single-label UI** | 2 | Upload + form + result panel via HTMX. Image preview. Sample-label button. Colored verdict banner. |
| **4. Batch flow (SSE)** | 3 | Alpine.js dropzone, SSE endpoint, per-row streaming, results table, CSV export, concurrency limit. |
| **5. OpenAI fallback + model swap** | 1.5 | Second extractor implementation behind same ABC. Env-var swap. Automatic fallback on Gemini error. |
| **6. Caching + polish** | 1 | LRU cache, error boundaries, loading states, accessibility pass. |
| **7. Eval suite** | 2.5 | Test set assembled (20 labels), eval harness, baseline numbers recorded. Run with both extractors. |
| **8. Deploy + smoke test** | 1 | Render deploy, env-var config, end-to-end on deployed URL, screenshots for README. |
| **9. README + writeup** | 2 | The thing that wins the interview. See §8. |
| **Buffer** | ~2 | Inevitable surprises (API quirks, deploy snags, eval failures) |

---

## 8. README structure (the thing that wins the interview)

11 sections, each short and specific.

1. **What this is** — one paragraph + screenshot of the deployed app
2. **Quick start** — ≤5 commands to run locally (`make dev`, `make eval`, etc.)
3. **Deployed demo** — link + "Try the sample label" instructions
4. **How it works** — the §3.1 architecture diagram + 1-2 paragraphs
5. **Why hybrid (AI extraction + deterministic verification)** — the writeup section that signals AI engineering judgment
6. **Regulatory grounding** — table of CFR sections referenced, why citing them matters for federal context
7. **Model selection: cost, latency, and federal procurement** — Gemini default, OpenAI fallback, GSA MAS / FedRAMP context, why the abstraction matters
8. **Handling stakeholder signals** — table mapping each Sarah / Dave / Jenny / Marcus signal to a concrete design decision
9. **Eval results** — actual numbers from the eval suite, including model A/B comparison, with frank discussion of failure modes
10. **What I'd do next in production** — PII handling, COLA integration considerations, observability, retraining cadence with agent feedback loop, human-in-the-loop for WARN verdicts, background job queue for batches >50, switching to GovCloud-pathed model (Bedrock for Claude or Vertex IL4 for Gemini)
11. **Trade-offs and limitations** — what's NOT in this prototype, why; including PDF support, multi-page handling, persistent storage, auth

---

## 9. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Gemini API latency variance pushes some labels over 5s | Medium | Set timeout, automatic OpenAI fallback, SSE so users see progress not a spinner, cache by image hash |
| Extraction prompt fragile across beverage types | High | Build eval suite early; iterate prompt against failures; few-shot examples in prompt |
| Government warning verbatim mismatch (text changed, multiple official variants) | Low (text confirmed from TTB.gov directly) | Source from `/eval/canonical_warning.txt`, cite 27 CFR 16.21 |
| Vision model "formatting" check is unreliable | Medium | Phrase as three discrete yes/no questions with explicit visual definitions; mark formatting verdict as WARN (not FAIL) if model confidence is medium |
| Batch flow times out on large uploads | Low at prototype scale | Concurrency limit (5); SSE keeps connection alive; document background-job path |
| Deployment hits free-tier limits during demo | Low | Render starter tier; pre-warm before submission; document cold-start expectation |
| Reviewer can't access deployed URL (firewall, expired free tier) | Medium | Include screenshots + 30s screen-recording GIF in README; provide one-command Docker run as backup |
| HTMX/SSE unfamiliar to reviewer | Low | The README "Architecture" section briefly notes "frontend uses HTMX (server-rendered HTML fragments) for simplicity — see [link]" |

---

## 10. Decisions log (for the writeup)

Decisions made during planning, worth surfacing in the README's "Trade-offs" section:

- **Hybrid architecture over pure-LLM** — explainability, auditability, regulatory grounding
- **Gemini primary + OpenAI fallback over Claude** — latency fits the 5s bar; both vendors on GSA MAS; pluggable abstraction lets the agency swap based on their approved-vendor list
- **Single Python service over two-service** — prototype simplicity, no CORS, faster deploy, no Node toolchain
- **HTMX over React/Next.js** — server-rendered HTML fits the form-and-result pattern; no client-side state machine needed; one codebase
- **SSE streaming over background jobs for batch** — single service, simple code, fits prototype scale; production path documented
- **In-memory LRU cache over Redis** — prototype only; production path documented
- **Rolled abstraction (`LabelExtractor` ABC) over LiteLLM** — ~30 lines, more architectural signal in the writeup
- **CFR citations inline in code + verdicts** — high signal for a Treasury role; demonstrates the developer actually read the regs

---

## 11. Definition of done

- [ ] Repo public on GitHub with clean commit history (meaningful messages, no `.env` committed, sensible `.gitignore`)
- [ ] README hits all 11 sections in §8
- [ ] Deployed URL works and processes a label end-to-end in ≤5s on cache miss, <100ms on cache hit
- [ ] Batch flow processes 10 labels in parallel with SSE live updates
- [ ] Eval suite runs with `make eval` and prints numeric results
- [ ] Both Gemini and OpenAI implementations work behind the same `LabelExtractor` interface; env-var swap
- [ ] All 7 TTB checklist fields verified, with beverage-type conditionality applied
- [ ] All 8 match-flavors handled (cosmetic, borderline, numeric tolerance, equivalent rep, warning formatting, just-over-tolerance, low confidence, disallowed abbreviation)
- [ ] Verdict reasons cite the specific 27 CFR section
- [ ] Stakeholder-signals table in README complete
- [ ] Sample label preloaded so reviewer can click once and see the flow
- [ ] Screenshots + GIF in README as deployment-failure backup
- [ ] No PII or sensitive data persisted
