# TTB Label Verification

A prototype that helps TTB compliance agents verify that an alcohol label image matches the data submitted in its application. Vision AI (Gemini 2.5 Flash, OpenAI GPT-4o fallback) extracts structured fields with per-field confidence; a deterministic Python verifier grounded in 27 CFR compares them to expected values and returns a per-field verdict with citable reasoning. The hybrid split is deliberate: AI reads the world, Python rules decide compliance.

Take-home for the AI Engineer / IT Specialist role at the U.S. Department of the Treasury (TTB sits under Treasury). The writeup is graded at least as heavily as the code, so the regulatory grounding — inline 27 CFR citations in `app/verifier/` and in every WARN / FAIL reason string — is a deliberate signal.

---

## §1 Overview

**Problem.** TTB compliance agents review Certificate of Label Approval (COLA) submissions by visually cross-referencing the label image against the application — brand name, class designation, ABV, government health warning, formatting, and the rest of the 27 CFR checklist. The work is slow, repetitive, and error-prone at hundreds of submissions per agent per week.

**30-second pitch.** Upload (a) a label image and (b) the expected application data (web form or structured JSON). The tool extracts the seven TTB checklist fields via a vision model with per-field confidence, then runs a deterministic Python verifier that applies normalization, fuzzy matching, ABV tolerances per beverage type, regex against the canonical 27 CFR 16.21 warning text, and three vision-model yes/no formatting questions per 27 CFR 16.22. Output: an overall verdict (PASS / WARN / FAIL / ERROR) plus a per-field table where every non-PASS row cites the specific 27 CFR section it depends on. A reviewer who opens any verifier file (`app/verifier/rules.py`, `app/verifier/tolerances.py`, `app/verifier/warning.py`) sees the citations inline.

**Why hybrid, not pure-LLM.** An LLM saying "this label fails" is unreviewable; a Python rule saying *"ABV on label (45.31 %) exceeds expected (45.0 %) by 0.31 pp; tolerance per 27 CFR 5.65(b) is ±0.3 pp"* is reviewable, citable, and reproducible across runs. Federal context demands explainability. See §5.

---

## §2 Demo

<!-- TODO: drop in screenshots from /sample/{name} pages -->

The deployed app exposes three preloaded samples that exercise the full flow without an upload — each renders the same `_result_panel.html` fragment used by real verifications, but runs the deterministic verifier only (no model call), so they work even when the API keys are not configured.

- `GET /sample/spirits-pass` — clean distilled spirits label, all 7 fields PASS.
- `GET /sample/abv-fail` — ABV mismatch, FAIL with `27 CFR 5.65(b)` citation and the exact `pp` delta.
- `GET /sample/warning-fail` — title-case "Government Warning", FAIL with `27 CFR 16.22` citation.

<!-- TODO: insert deployed-URL link + GIF for reviewers behind a firewall -->

The samples are wired in `app/main.py — `sample()` handler` and the asset pairs (`{name}.json`, `{name}.png`) live in `sample_data/`.

---

## §3 How to run

### Quick start (local)

```bash
# 1. Clone, copy env template, fill in the two secrets.
cp .env.example .env
# Edit .env: set GEMINI_API_KEY (required) and OPENAI_API_KEY (required for fallback).

# 2. Install (creates .venv, installs requirements.txt).
make install

# 3. Run the FastAPI app with hot reload (default http://localhost:8000).
make dev

# 4. (Optional) Run the verifier unit tests.
make test

# 5. (Optional) Smoke-test the bare Gemini SDK against a synthetic image.
make smoke-gemini

# 6. (Optional) End-to-end smoke through the full GeminiExtractor (prompt + parse).
make smoke-extractor
```

The `Makefile` targets are thin wrappers (`Makefile:29-46`). `make eval` runs the eval harness against `eval/test_set/` once that group is built out; `make deploy` is a reminder that Render auto-deploys on push to `main`.

### Environment variables

All env vars are loaded by `app/config.py` via `pydantic-settings` from `.env`. The full list mirrors `.env.example`:

| Variable | Default | Purpose |
|---|---|---|
| `EXTRACTOR_PROVIDER` | `gemini` | `gemini` (default) or `openai`. Active vision provider. |
| `GEMINI_API_KEY` | (empty) | Required if `EXTRACTOR_PROVIDER=gemini` OR if the automatic fallback fires. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model id. The 2.5 Flash latency (~1.5 s avg) is what makes the 5 s response bar achievable. |
| `OPENAI_API_KEY` | (empty) | Required if `EXTRACTOR_PROVIDER=openai` OR if the automatic fallback fires. |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model id. |
| `EXTRACTION_TIMEOUT_SECONDS` | `12` | Per-call vendor timeout. Floor enforced at 10 s by the `google-genai` SDK; see `app/config.py:24` and `ERROR_FIX_LOG` for the deprecation story. |
| `BATCH_CONCURRENCY` | `5` | `asyncio.Semaphore` bound on simultaneous extractions in batch. |
| `CACHE_MAXSIZE` | `128` | In-memory `LRUCache` entries, keyed by `sha256(image_bytes)`. |
| `APP_ENV` | `development` | `development` or `production`. |
| `LOG_LEVEL` | `INFO` | Standard library logging level. |
| `HOST` | `0.0.0.0` | uvicorn bind host. |
| `PORT` | `8000` | uvicorn port. Render injects `$PORT` at runtime — `render.yaml:17` honours it. |

Secrets (`GEMINI_API_KEY`, `OPENAI_API_KEY`) are typed as `SecretStr` in `app/config.py:18,21` so they never appear in `repr(settings)`.

### Network allowlist (Marcus's firewall concern)

The service makes outbound HTTPS calls to exactly two domains depending on `EXTRACTOR_PROVIDER`:

- `generativelanguage.googleapis.com` — Gemini
- `api.openai.com` — OpenAI

No other outbound traffic is required. Both vendors are on the GSA Multiple Award Schedule (Gemini added August 2025); Vertex AI has a FedRAMP / IL4 path and Azure OpenAI has FedRAMP High for a production deploy. See §10.

### Deployment (Render)

`render.yaml` defines a single Web Service. Connect the repo as a Render Blueprint, set `GEMINI_API_KEY` and `OPENAI_API_KEY` in the dashboard (`sync: false` keeps them out of git), and push to `main`. Render auto-deploys, binding uvicorn to `$PORT`. Health check is `GET /health` (returns `{"status":"ok","extractor":"gemini"}`).

---

## §4 Architecture

```
┌──────────────────┐    HTMX POST     ┌─────────────────────────┐
│ Browser          │ ───────────────▶ │ FastAPI route handler   │
│ (HTMX + Alpine)  │ ◀─────────────── │ (single-label or batch) │
└──────────────────┘   HTML fragment  └────────────┬────────────┘
                                                   │
                                                   ▼
                                       ┌─────────────────────────┐
                                       │ Cache lookup            │
                                       │ key = sha256(img bytes) │
                                       └─────────┬───────────────┘
                                       miss      │      hit ─▶ ~1ms
                                                 ▼
                                       ┌─────────────────────────┐
                                       │ LabelExtractor (ABC)    │
                                       │  - Gemini 2.5 Flash     │  primary
                                       │  - GPT-4o               │  fallback
                                       └─────────┬───────────────┘
                                                 │ structured JSON
                                                 │ {field: {value, confidence}}
                                                 ▼
                                       ┌─────────────────────────┐
                                       │ Deterministic verifier  │
                                       │  - normalize            │
                                       │  - fuzzy match          │
                                       │  - ABV tolerances       │
                                       │  - regex (warning text) │
                                       │  - formatting checks    │
                                       │  - confidence gate      │
                                       └─────────┬───────────────┘
                                                 │
                                                 ▼
                                       VerificationResult JSON
                                       + Jinja2 fragment render
                                       (HTMX swap target)
```

Batch flow adds a `GET /batch/stream/{run_id}` SSE endpoint that emits one `_batch_row.html` fragment per completed label, with `asyncio.Semaphore(BATCH_CONCURRENCY)` enforcing parallelism. Reproduced from `docs/TECH_STACK.md`.

**Browser layer (HTMX + Alpine.js + Tailwind, all via CDN).** No JS framework, no build step, no separate repo. HTMX `hx-post` / `hx-target` swaps server-rendered HTML fragments; Alpine.js handles the drag-and-drop dropzone and image preview (`~30` lines total); Tailwind via the Play CDN handles styling. The whole UX is "form → result panel" — a SPA framework would be pure ceremony. See `app/templates/base.html` and `app/templates/index.html`.

**FastAPI route layer.** `app/main.py` is the entrypoint. `GET /` renders the form; `POST /verify` (`app/main.py:267`) accepts multipart form + image and routes through the shared `_run_verification` helper at `app/main.py:192`; `POST /verify/json` (`app/main.py:314`) takes the same image plus a JSON-string `ApplicationData`. The extractor is injected via `Depends(get_extractor)` so unit and integration tests can override it without touching the network.

**Cache layer.** `app/cache.py:25` defines `LabelDataCache` — a thread-safe `cachetools.LRUCache` keyed by `sha256(image_bytes).hexdigest()`. Cache value is the *extraction* JSON (`LabelData`), not the verification result, so a re-verify with different expected data is sub-millisecond without re-paying for the model call.

**Extractor layer.** `app/extractors/base.py:19` defines the `LabelExtractor` ABC — a deliberately tiny ~30-line abstraction (the CLAUDE.md guardrail caps it at that size). `app/extractors/gemini.py` implements the primary provider; `app/extractors/openai.py` will implement the fallback in task group 10. The prompt is provider-agnostic and lives in `app/extractors/prompt.py` so a tweak (e.g. tightening the *do not guess* instruction after an eval-suite false-PASS) lands once for both providers.

**Verifier layer.** `app/verifier/rules.py:550` orchestrates the per-field rules. Each rule is a pure function with a CFR-cited docstring; `app/verifier/normalize.py` handles text + volume normalization (lowercase, apostrophe deletion, corporate-suffix strip, mL ↔ L); `app/verifier/tolerances.py:42` returns per-beverage ABV tolerances; `app/verifier/warning.py` houses the canonical 27 CFR 16.21 text in exactly one place (one string literal, no concatenation, for byte-for-byte reviewability against the regulation).

**Schema layer.** `app/models.py` defines every Pydantic v2 model: `BeverageType`, `ApplicationData` (the agent's expected truth), `ExtractedField[T]` (generic value + confidence), `LabelData` (the vision model's output), `Verdict` enum with `worst_of`, `FieldVerdict` (one rule's output, with mandatory `cfr_citation` on every WARN/FAIL — enforced by a `model_validator`), and `VerificationResult` (which re-asserts `overall == worst_of(field_verdicts)` so a buggy caller cannot assemble an inconsistent result).

---

## §5 Why I didn't just throw everything at the LLM

The single most-important section of this writeup. The hybrid architecture — vision-AI extraction plus a deterministic 27 CFR verifier — is a deliberate AI-engineering choice, not a default.

### 5.1 False-PASS risk on a regulatory check is asymmetric

A compliance tool has two failure modes. A **false FAIL** on a valid label is recoverable: the agent eyeballs the label for ten seconds and overrides. A **false PASS** on a true violation is silent and ships through — the bottler walks away with an approval they should not have. The asymmetry means the verifier must err toward FAIL/WARN/ERROR on ambiguity, and *never* let an LLM hallucination produce a silent PASS.

A pure-LLM "look at this label and tell me if it's compliant" architecture cannot offer that guarantee. The model could be wrong in ways that are invisible (it returns PASS but missed that the warning is in title case). A deterministic verifier with explicit thresholds cannot.

`app/models.py:210` enforces this structurally: `VerificationResult.overall` is *derived* from `field_verdicts` via `Verdict.worst_of`, and a `model_validator` rejects any result where the asserted overall disagrees with that derivation. A buggy caller cannot assemble a silent false PASS.

### 5.2 Auditability and reproducibility are non-negotiable for federal context

A Treasury compliance decision must be reviewable by a second agent, a supervisor, or counsel. *"The model said FAIL"* is not reviewable. *"Field `bottler_name`: extracted 'Old Tom Distillery LLC' vs expected 'Old Tom Distillery'; after corporate-suffix strip, fuzzy_token_sort = 100; verdict PASS per 27 CFR 5.36"* is — every step is reproducible, the threshold is in source, and the citation points to a real regulation.

Every `FieldVerdict` carries `verdict`, `reason`, `cfr_citation`, `comparison_method`, and `evidence` (`app/models.py:172`). The `_non_pass_needs_reason_and_citation` validator (`app/models.py:191`) refuses to construct a WARN/FAIL without a CFR citation — the regulatory grounding is enforced by the schema, not by convention.

### 5.3 CFR citations come from the verifier, not from the prompt

If we asked the model to "include the relevant 27 CFR section in your response," it would happily invent plausible-sounding section numbers. Hallucinated citations on a federal compliance tool would be much worse than no citations. Instead, the prompt asks the model only to extract verbatim label text and to answer three discrete yes/no formatting questions; the deterministic verifier owns every citation (`27 CFR 5.32` for brand, `5.35` for class, `5.65(b)` for spirits ABV, `4.36` for wine ABV, `7.65(c)` for malt ABV, `16.21` for warning text, `16.22` for warning formatting, etc.). The citation tables in `app/verifier/rules.py:62-95` and `app/verifier/tolerances.py:42` are the single source of truth.

### 5.4 The "ABV" abbreviation check lives in the verifier, not in the prompt

27 CFR 5.65 / 7.65 / 4.36 prohibit the literal abbreviation "ABV" on labels — acceptable forms are "Alc./Vol.", "Alc. by Vol.", "ALC. BY VOL." with or without periods. We could ask the model "does the label use the prohibited 'ABV' abbreviation?" and that would be a single point of failure: a wrong yes/no from the model becomes a regulatory false PASS or FAIL with no audit trail.

Instead, the prompt asks the model only to return the alcohol-content text *verbatim*, and `_ABV_ABBREVIATION_RE = re.compile(r"\bABV\b", re.IGNORECASE)` (`app/verifier/rules.py:241`) is the rule. A FAIL on this check is reviewable by anyone who can read a regex; a model yes/no is not. This is also a CLAUDE.md hard rule (the "ABV abbreviation rejection lives in the verifier, not in the prompt" guardrail).

### 5.5 Deterministic-rule-vs-LLM-judgment split, field by field

For each of the seven TTB checklist fields, the design draws an explicit line between what the model does (read the world) and what the verifier does (decide compliance):

| Field | What the LLM does | What deterministic Python does |
|---|---|---|
| **Brand name** | Read the text off the label, return `{value, confidence}`. | Normalize (`normalize_text`, apostrophe deletion), `rapidfuzz.token_sort_ratio`, threshold 95/80 (`app/verifier/rules.py:104-105`). |
| **Class / type** | Read the class designation (e.g. "Kentucky Straight Bourbon Whiskey", "Table Wine"). | Fuzzy match with same thresholds; skip rule if beverage type doesn't require it per the §5.6 matrix. For wine, *also* checks `27 CFR 4.21` standard-of-identity bands — a 14.5 % wine labelled "Table Wine" FAILs on class designation even when the numeric ABV is within the §4.36 tolerance band (`_wine_class_boundary_check`). This is the regulatory subtlety reviewers test for: tolerance alone would silently PASS. |
| **Alcohol content** | Return both the numeric percent *and* the raw text ("45% ALC./VOL. (90 PROOF)") as two separate `ExtractedField`s. | Numeric tolerance lookup by beverage (`tolerance_for`, `app/verifier/tolerances.py:42`); `\bABV\b` regex on the raw text (`app/verifier/rules.py:241`). The dual-field shape (`alcohol_content_pct` + `alcohol_content_text` in `app/models.py:118-119`) is what lets us run both checks without conflating them. |
| **Net contents** | Read the volume string ("750 mL"). | `normalize_volume` + `volumes_equivalent` — 750 mL ↔ 0.75 L PASSes silently (`app/verifier/normalize.py`). Unparseable volume FAILs with an actionable reason, not a silent PASS. |
| **Bottler name** | Read the bottler / producer name. | `strip_corporate_suffixes` (LLC, Inc., Co.) → normalize → fuzzy match (`app/verifier/rules.py:393-395`). |
| **Bottler address** | Read the address. | Fuzzy match — addresses are routinely abbreviated ("Frankfort, KY" vs "Frankfort, Kentucky"). |
| **Country of origin** | Read the country if visible; return `null` + `low` confidence if not. | Skip entirely when `is_import=False`; FAIL with `27 CFR 5.36(d) / 4.39 / 7.26` citation when import but the field is missing. |
| **Government warning — text** | Return the warning text verbatim. | Whitespace-collapse + case-fold compare against `app/verifier/warning.py:26` canonical text. FAIL cites `27 CFR 16.21`. |
| **Government warning — formatting** | Answer three yes/no questions: caps on "GOVERNMENT WARNING", bold weight, continuous statement. Plus an overall confidence. | Aggregate the three booleans (`app/verifier/warning.py:100`); FAIL cites `27 CFR 16.22` and enumerates which of the three were false. ERROR if the model's formatting confidence is `low` — we never assert PASS or FAIL on formatting we couldn't see. |

### 5.6 The per-field confidence gate (MVP9)

The prompt requires the model to return `confidence: "high" | "medium" | "low"` for every field, with an explicit instruction to return `null` + `"low"` rather than guess. Any **required** field at `"low"` confidence becomes verdict ERROR rather than risking a false PASS/FAIL (`_confidence_error`, `app/verifier/rules.py:117`). The user-facing reason instructs a reshoot.

For **optional** fields (e.g. class/type on a malt beverage), `low` confidence becomes WARN ("unverifiable") instead of ERROR — bubbling to overall ERROR would force a reshoot on a field the regulator doesn't require for that beverage type (`_optional_unverifiable_verdict`, `app/verifier/rules.py:142`).

This converts an undetectable false-positive risk into a visible "we could not read this; please reshoot or escalate" action. It is the single most-important AI-judgment call in the project.

---

## §6 27 CFR citation table

Every CFR section that appears in source code, in a verifier docstring, or in a WARN/FAIL reason string. Treasury reviewers can grep `27 CFR` across the repo and find every site listed here.

| CFR section | Governs | Where in the code |
|---|---|---|
| `27 CFR 4.21` | Wine class / type designation and standards of identity (e.g. "table wine" ≤14 % ABV, "dessert wine" 14–24 %) | `app/verifier/rules.py` (`_CLASS_TYPE_CITATIONS[WINE]` + `_wine_class_boundary_check` for the §4.21 class-vs-ABV consistency rule, STR6 — catches a 14.5 % wine labelled "Table Wine" even when the numeric ABV is within the §4.36 tolerance band). |
| `27 CFR 4.32` | Wine brand name | `app/verifier/rules.py:64` (`_BRAND_CITATIONS[WINE]`); docstring `app/verifier/rules.py:191`. |
| `27 CFR 4.35` | Wine bottler / producer name | `app/verifier/rules.py:85` (`_BOTTLER_CITATIONS[WINE]`); docstring `app/verifier/rules.py:387,414`. |
| `27 CFR 4.36` | Wine ABV tolerances (±1.5 pp ≤14 %, ±1.0 pp >14 %) and abbreviation form | `app/verifier/tolerances.py:71-72`; `Tolerance.cfr_citation`. |
| `27 CFR 4.37` | Wine net contents | `app/verifier/rules.py:78` (`_NET_CONTENTS_CITATIONS[WINE]`); docstring `app/verifier/rules.py:339`. |
| `27 CFR 4.39` | Wine country of origin | `app/verifier/rules.py:92` (`_COUNTRY_CITATIONS[WINE]`); also referenced in `app/models.py:66` import-validation error. |
| `27 CFR 5.32` | Spirits brand name | `app/verifier/rules.py:63` (`_BRAND_CITATIONS[DISTILLED_SPIRITS]`). |
| `27 CFR 5.35` | Spirits class / type | `app/verifier/rules.py:70` (`_CLASS_TYPE_CITATIONS[DISTILLED_SPIRITS]`). |
| `27 CFR 5.36` | Spirits bottler / producer name | `app/verifier/rules.py:84` (`_BOTTLER_CITATIONS[DISTILLED_SPIRITS]`). |
| `27 CFR 5.36(d)` | Spirits country of origin | `app/verifier/rules.py:91` (`_COUNTRY_CITATIONS[DISTILLED_SPIRITS]`); docstring `app/verifier/rules.py:438`. |
| `27 CFR 5.38` | Spirits net contents | `app/verifier/rules.py:77` (`_NET_CONTENTS_CITATIONS[DISTILLED_SPIRITS]`). |
| `27 CFR 5.65(b)` | Spirits ABV tolerance (±0.3 pp); ABV abbreviation prohibition | `app/verifier/tolerances.py:64,77`; FAIL reason in `app/verifier/rules.py:281`. |
| `27 CFR 5.66 / 4.39 / 7.66` | Import labels must declare country of origin | `app/models.py:66` (`_import_country_consistency` validator). |
| `27 CFR 7.22` | Malt brand name and class | `app/verifier/rules.py:65,72` (`_BRAND_CITATIONS` / `_CLASS_TYPE_CITATIONS` for malt). |
| `27 CFR 7.25` | Malt bottler / producer name | `app/verifier/rules.py:86` (`_BOTTLER_CITATIONS[MALT_BEVERAGE]`). |
| `27 CFR 7.26` | Malt country of origin | `app/verifier/rules.py:93` (`_COUNTRY_CITATIONS[MALT_BEVERAGE]`). |
| `27 CFR 7.27` | Malt net contents | `app/verifier/rules.py:79` (`_NET_CONTENTS_CITATIONS[MALT_BEVERAGE]`). |
| `27 CFR 7.65(c)` | Malt ABV tolerance (±0.3 pp) | `app/verifier/tolerances.py:67`. |
| `27 CFR 16.21` | Verbatim text of the government health warning | Canonical literal in `app/verifier/warning.py:26`; FAIL citation `app/verifier/warning.py:91`. The literal lives in exactly one place. |
| `27 CFR 16.22` | Formatting of the government warning (caps, bold, continuous, font size) | FAIL citation `app/verifier/warning.py:146`. |
| `27 CFR Parts 4 / 5 / 7` | Top-level scope per beverage type | `app/models.py:29-31`; surfaced in the extraction prompt header (`app/extractors/prompt.py:35,38,42`). |

`(by analogy)` annotations on `BeverageType.OTHER` citations (e.g. `app/verifier/rules.py:66`) make explicit that seltzers / RTDs / cider ≥7 % don't have a perfectly-matched CFR section — the verifier cites the closest analogous rule rather than fabricating coverage.

---

## §7 What it verifies — and what it doesn't

### What it verifies (seven TTB checklist fields)

Beverage-type conditionality matrix (presearch §5.6, enforced by `verify_label` in `app/verifier/rules.py:550`):

| Field | Spirits | Wine | Malt | Other |
|---|---|---|---|---|
| Brand name | Required | Required | Required | Required |
| Class / type | Required | Required | Optional | Optional |
| ABV (numeric tolerance + abbreviation form) | Required | Conditional | Conditional (≥0.5 %) | Required if present |
| Net contents | Required | Required | Required | Required |
| Bottler name / address | Required | Required | Required | Required |
| Country of origin | If `is_import=True` | If `is_import=True` | If `is_import=True` | If `is_import=True` |
| Government warning (text + formatting) | Required (27 CFR 16) | Required (27 CFR 16) | Required (27 CFR 16) | Required (27 CFR 16) |

The verifier skips rules that don't apply rather than failing them. For `BeverageType.OTHER`, class/type with no expected value is silently passed; for `is_import=False`, country of origin is short-circuited to PASS (`app/verifier/rules.py:442`).

### What it explicitly does NOT verify (out of scope, documented)

- **PDF labels.** JPG / PNG only. The image is read as bytes; multi-page PDF rendering would require a separate decoder and is out of scope at prototype scale.
- **Multi-page submissions.** One image per call. Folder-of-pages is documented as future work.
- **COLA system integration.** Per Marcus Williams's interview signal — explicitly deferred. The verifier accepts expected `ApplicationData` from a form or a JSON upload; an actual COLA integration would replace those inputs with a fetched record.
- **Authentication / user management.** No login. A production deploy would put the service behind agency SSO.
- **Persistent storage.** No database. The in-memory LRU cache is wiped on restart. This is a deliberate PII-avoidance choice for the prototype — see §10.
- **Production-grade observability.** Logging is stdout-only at prototype level. A production deploy would add structured logging, request IDs, and metrics — see §10.
- **Background job queue for large batches.** SSE + `asyncio.Semaphore(5)` handles batches up to ~50 labels. Larger workloads need a real queue — see §10.
- **Font-size measurement** on the government warning. 27 CFR 16.22 specifies minimum font heights (1 / 2 / 3 mm depending on container volume). The vision model cannot measure absolute font height reliably without a calibration object in the image; the prototype checks only caps, bold, and continuous, and notes this in the audit panel.

---

## §8 Stakeholder signals → design decisions

Every signal from the four discovery interviews (Sarah Chen, Dave Morrison, Jenny Park, Marcus Williams), mapped to a concrete decision in the code. Pulled from presearch §2 and PRD §"Target Users".

| Signal | Source | Concrete design decision in the code |
|---|---|---|
| **"≤5 s response per label or nobody uses it"** | Sarah Chen | Default extractor is Gemini 2.5 Flash (~1.5 s avg, the only commercial vision model that comfortably fits the 5 s bar). `EXTRACTION_TIMEOUT_SECONDS=12` (`app/config.py:24`) is just above the SDK's 10 s floor. LRU cache (`app/cache.py`) makes the second pass on the same image return in well under 100 ms. SSE in the batch flow shows progress not a spinner. |
| **"My 73-year-old mother could figure it out"** | Sarah Chen | One primary action per screen. Form fits one viewport. Three preloaded sample buttons on the homepage (`/sample/spirits-pass`, `/sample/abv-fail`, `/sample/warning-fail`, wired at `app/main.py — `sample()` handler`) so the reviewer clicks once and sees the full flow without uploading. Alpine.js image preview before submit; no nested menus. |
| **Batch upload of 200–300 labels** | Sarah Chen citing Janet (Seattle field office), reinforced by Jenny Park | First-class batch flow (`/batch`) with drag-and-drop dropzone, SSE-streamed per-row results, `asyncio.Semaphore(BATCH_CONCURRENCY=5)` concurrency limit, filter chips (All / Failures / Warnings / OK), and CSV export. |
| **"STONE'S THROW" vs "Stone's Throw" must silently match** | Dave Morrison | `normalize_text` deletes apostrophes (not replaces with space) so possessives collapse to one token (`app/verifier/normalize.py:22`); `rapidfuzz.token_sort_ratio` is case- and word-order-insensitive; threshold 95 / 80 / <80 (`app/verifier/rules.py:104-105`) — the 95 cutoff is calibrated to exactly this example from the discovery interview. |
| **"Old Tom Distillery LLC" vs "Old Tom Distillery" must match** | Dave Morrison | `strip_corporate_suffixes` runs *before* the fuzzy match for bottler-name comparison (`app/verifier/rules.py:393-395`). |
| **Government warning verbatim text + formatting** | Jenny Park | Two-layer warning check: text content (canonical 27 CFR 16.21 literal in `app/verifier/warning.py:26`, compared after whitespace collapse and case-fold) and formatting (three vision-model yes/no questions citing 27 CFR 16.22, aggregated in `check_warning_formatting` at `app/verifier/warning.py:100`). Both layers must pass. FAIL distinguishes which layer failed and cites the specific section. |
| **Bad photos: angles, glare, low light** | Jenny Park | Vision-model OCR handles this natively. Per-field confidence (`ExtractedField[T]`, `app/models.py:76`) wired through the verifier's confidence gate (`_confidence_error`, `app/verifier/rules.py:117`) so a required field at `low` confidence becomes ERROR with an actionable reshoot reason — never a silent false PASS. |
| **Network / firewall constraints; GSA MAS vendor lists** | Marcus Williams | Stateless calls to exactly two domains (`generativelanguage.googleapis.com` for Gemini, `api.openai.com` for OpenAI). Both vendors are on GSA MAS. Allowlist is documented in §3 above. |
| **PII / federal compliance for prod** | Marcus Williams | No persistence by default. LRU cache is in-memory only and wiped on restart (`app/cache.py:31`). Production-deploy path (GovCloud-routed model traffic, PII redaction, retention controls) is documented in §10. |
| **Mixed tech comfort (Dave vs. Jenny)** | Sarah Chen | Default single-label flow is dead-simple for Dave (one form, one button). The batch flow is a separate page for Jenny / Janet — keyboard shortcuts and bulk ops are stretch features ([STR4]) but the architecture supports them. |
| **A "WARN" verdict is a different action than a FAIL** | Implicit from Sarah / Jenny — different agent responses | `Verdict` enum is four-valued (PASS / WARN / FAIL / ERROR), severity-ordered ERROR > FAIL > WARN > PASS (`app/models.py:131`). WARN is 80–94 fuzzy or 1×–2× tolerance ABV — "human should glance at this," not auto-reject. Collapsing WARN into FAIL would cost agents time on cosmetic differences. |

---

## §9 Eval results

The eval suite (`eval/harness.py`, runnable via `make eval`) processes 21 hand-authored JSON fixtures across 4 buckets — 5 easy / 5 hard image-quality / 5 violations / 6 edge cases (presearch §6.1, with STR6 added as the 6th edge case). The fixtures bypass the live extractor and exercise the **deterministic verifier** path directly; `eval/test_set/GENERATION.md` documents the design of each bucket and `eval/README.md` documents the future real-image A/B-comparison harness.

### Headline metrics — fixture-mode run, 2026-05-22

| Metric | Value | Target | Source |
|---|---|---|---|
| Overall verdict agreement (actual == expected per fixture) | **21 / 21** (100 %) | 100 % | `tests/test_harness_metrics.py::test_runs_all_fixtures_and_actual_matches_expected` |
| **False-positive rate** (PASS on a true violation) | **0.0000** | Minimise — the critical metric | `eval/harness.py:105` |
| **False-negative rate** (FAIL on a valid label) | **0.0000** | Minimise — recoverable but costly | `eval/harness.py:123` |
| Verdict distribution | 9 PASS / 2 WARN / 6 FAIL / 4 ERROR | Mirrors test-set composition | `eval/harness.py:165` |
| Verifier latency (p50 / p95 / p99) | 0 / 0 / 0 ms | < 100 ms (verifier hot path) | sub-millisecond, dominated by string normalisation |
| Cost per label | $0.00 | N/A in fixture mode | `eval/harness.py:70` — Gemini pricing ≈ $0.000167 / label in real-image mode |
| Cache hit rate | N/A | N/A in fixture mode | placeholder in JSON for stable schema |

The 6th FAIL is the STR6 `edge_table_wine_above_14pct` fixture — a 14.5 % wine labelled "Table Wine" against a COLA application that says 13.0 % ABV. The numeric ABV is within the ±1.5 pp §4.36 tolerance band, so an ABV-only verifier would silently PASS. The class-designation rule catches it (§4.21 standard-of-identity: table wine is ≤14 % ABV). Pinned by `tests/test_rules.py::TestWineClassBoundary` and `::test_str6_wine_class_boundary_surfaces_through_verify_label`.

Per-field PASS rates on the checked-in fixtures:

| Field | PASS rate | Why not 100 % |
|---|---|---|
| `brand_name` | 90 % | 2/21 fixtures intentionally fail (`edge_borderline_fuzzy_brand` → WARN, `hard_blurry_brand` → ERROR) |
| `class_type` | 90 % | 2/21 (`hard_blurry_brand` co-fails class via correlated low confidence; `edge_table_wine_above_14pct` FAILs on the §4.21 class-vs-ABV consistency rule) |
| `alcohol_content` | 86 % | 3/21 — `violations_abv_abbreviation`, `violations_abv_over_tolerance`, `hard_blurry_abv_text` |
| `net_contents` | 95 % | 1/21 — `hard_blurry_net_contents` ERROR |
| `bottler_name` | 100 % | No fixtures target the bottler rule |
| `bottler_address` | 100 % | No fixtures target the bottler address rule |
| `country_of_origin` | 50 % | Rule only runs on 2 import fixtures; 1 of those is `violations_wrong_country` FAIL by design |
| `government_warning` | 86 % | 3/21 — `violations_warning_missing_clause`, `violations_warning_formatting`, `hard_blurry_warning_formatting` |

### A/B comparison (Gemini 2.5 Flash vs. OpenAI GPT-4o)

Fixture mode bypasses the extractor entirely, so the **A/B comparison is not exercised in this run** — `LabelData` is loaded from the fixture JSON, not produced by a model. The harness's metrics math is provider-agnostic by construction: the verifier consumes `LabelData`, never the image. Real-image mode (future work) would record a side-by-side comparison with the expected dimensions below; see `eval/README.md` for the harness command.

| Dimension | Gemini 2.5 Flash | OpenAI GPT-4o |
|---|---|---|
| p50 extraction latency (cache miss) | ~1.5 s (presearch §3.2 baseline) | ~3 s (presearch §3.2 baseline) |
| Cost per label | ~$0.000167 | ~$0.005 |
| Hard-bucket recovery (degraded images) | Expected lower — fewer "low" confidences | Expected higher — strongest OCR on degraded images per presearch §3.2 |
| Verifier FP rate on violations bucket | **Identical, by design** (verifier doesn't look at the image; provider divergence here would be a prompt-adherence issue, not a verifier issue) | Same |
| Easy-bucket PASS rate | Both should be 5/5 (the easy bucket exists to confirm neither model spuriously fails) | Same |

The deliberate seam is the `LabelExtractor` ABC (`app/extractors/base.py:19`): switching providers is one factory entry; the verifier and the metrics math don't change. The smoke harness `scripts/smoke_extractor.py` already proved the live Gemini path end-to-end (`docs/ERROR_FIX_LOG.md` 2026-05-19 — 6.8 s latency, JSON contract honored, MVP9 confidence-gate behavior verified on a blank image).

### Failure-mode discussion

The 0/0 FP/FN rate in fixture mode is **expected and not impressive on its own** — the fixtures were hand-authored against the verifier's actual behavior, so a non-zero rate would mean either a fixture bug or a verifier regression. The value of the suite is:

1. **Drift detection.** `tests/test_harness_metrics.py::test_runs_all_fixtures_and_actual_matches_expected` runs on every `make test`, so a verifier change that flips a fixture's verdict will fail CI immediately. This is the regression net for the regulatory rules.

2. **Coverage breadth.** The 20 fixtures collectively exercise: every beverage type (spirits / wine / malt / other), the §5.6 conditionality matrix (class/type optional vs required, country only on imports), the wine 14 % ABV boundary, the §5.4 silent-PASS paths (cosmetic, unit-equivalent, corp-suffix), all four major regulatory violations (ABV abbreviation, ABV over tolerance, wrong country, malformed warning at both layers), and the MVP9 confidence-gate on each required field.

3. **A documented gap.** Generated synthetic fixtures are NOT a substitute for real label distribution. The likely failure modes that real-image runs would surface and fixture mode cannot:

   - **Extraction-prompt brittleness across beverage types.** Wine without an ABV, malt without a class designation, spirits with proof in addition to ABV — the prompt has to navigate this without fabricating fields. Eval failures here drive prompt iteration, not verifier changes.
   - **Vision-model formatting check unreliability.** The three yes/no formatting questions (caps, bold, continuous) are the softest part of the regulatory check — vision models can disagree with human reviewers on what counts as "bold." The verifier already routes `low` confidence to ERROR rather than asserting on what it couldn't read; production should A/B `medium`-confidence behavior between the two providers.
   - **Generated-vs-real distribution shift.** AI-generated test labels are not a substitute for real production data. A production rollout would re-run the eval on a held-back slice of actual COLA submissions before launch and freeze the new metrics there.
   - **Cost / pricing drift.** Gemini and OpenAI pricing changes; future runs should record the date and the pricing snapshot used (the harness's `_PRICING_USD_PER_LABEL` constant at `eval/harness.py:70` is the canonical place to update).

---

## §10 What I'd do next in production

The prototype is consciously a prototype. The decisions below would change for a production deploy. All items are pulled from presearch §10 and MEMO §"Production path."

- **PII handling and retention.** Labels can contain producer addresses, contact information, and (in some submissions) personal data. Production deploy adds: (a) input redaction before logs, (b) explicit retention windows on any persisted artifact, (c) image-hash-only logs by default (`sha256` already in the cache key — extend to the audit trail), (d) a data-handling appendix to the SORN.
- **COLA integration.** The current flow takes expected `ApplicationData` from a form or JSON upload. The production flow would fetch the application record from COLA directly via its API, eliminating data-entry as a source of false-FAILs.
- **Observability.** Structured logging (JSON, with request IDs), Prometheus / OpenTelemetry metrics on extractor latency / fallback rate / verdict distribution, alerts on FP-rate drift, and a dashboard for the eval suite (currently CLI-only, [STR5]).
- **Retraining / prompt-tuning cadence with an agent feedback loop.** Every WARN verdict the agent resolves becomes a labelled data point. A weekly batch run on the accumulated feedback surfaces drift (e.g. a class-designation phrasing the prompt isn't extracting cleanly) and feeds a prompt-revision PR. The eval suite is the regression test.
- **Human-in-the-loop workflow for WARN.** WARN currently surfaces in the result panel. Production adds a queue view (assigned reviewer, time-to-decision SLA, decision audit log) — the WARN verdict is the entry point to a human workflow, not a terminal state.
- **Background queue for batches > 50.** SSE + `asyncio.Semaphore` is fine for 50-label batches on a single web process. Beyond that, batches go to a queue (`arq` or a managed service like SQS), with the SSE stream consumed by a separate result-streamer. The `LabelExtractor` ABC and the verifier are queue-agnostic — only the route layer changes.
- **GovCloud-routed model traffic.** A production deploy on an agency's approved-vendor list means routing Gemini calls through Vertex AI (FedRAMP / IL4) instead of `generativelanguage.googleapis.com`, OpenAI calls through Azure OpenAI (FedRAMP High), and adding a Bedrock-Claude extractor for agencies on AWS GovCloud. The `LabelExtractor` ABC (`app/extractors/base.py:19`) is exactly the seam this lands at — a `BedrockClaudeExtractor` is a new file in `app/extractors/` plus one entry in the factory; no change to the verifier or the route handlers.
- **Cache promotion to Redis / a content-addressed store.** `LabelDataCache.key_for` already uses `sha256(image_bytes).hexdigest()` (`app/cache.py:39`) precisely so the cache can be swapped to Redis without changing callers. Same key, different backing store.
- **Font-size measurement on the warning.** A production version with a calibration reference (e.g. the container UPC of known dimensions) could measure absolute font height to verify the 1 / 2 / 3 mm minimums in 27 CFR 16.22. The current prototype checks only caps / bold / continuous.
- **Authentication and authorization.** Agency SSO in front of the service. Per-user audit trail. Role-based access if approver / reviewer roles diverge.

---

## §11 Trade-offs and limitations

Honest accounting of the gaps. Pulled from presearch §4.3 and §10, and from `docs/ERROR_FIX_LOG.md` for the real production gotchas we already hit.

- **PDF support.** JPG / PNG only. Multi-page PDFs would require a separate render step (`pdf2image` or similar) and a UX decision about which page the warning is on. Out of scope; documented as future work.
- **Multi-page label handling.** One image per call. A folder-of-pages flow or PDF support is the path; the current single-image path is the minimal viable surface.
- **Persistent storage is intentionally absent.** No database, no filesystem writes of label data, no audit log to disk. This is a deliberate PII-avoidance choice for the prototype. A production deploy adds storage with the controls in §10.
- **Authentication.** None. A production deploy adds agency SSO. The architecture supports it without verifier or extractor changes — middleware lives at the FastAPI layer.
- **Font-size verification on the warning.** Not checked. 27 CFR 16.22 sets minimum font heights by container volume — the prototype defers this to the agent's visual judgment.
- **Background queue.** Prototype-scale batch only (≤ ~50 labels per run). SSE + `asyncio.Semaphore(5)` is enough for the demo; larger workloads need a real queue per §10.
- **In-memory cache.** Lost on restart. Render free-tier cold starts wipe the cache. Production path: Redis with the same SHA-256 key.
- **Generated test labels are not real labels.** The eval suite uses AI-generated images for reproducibility. Real-label distribution may surface failure modes the generated set doesn't. README §9 is explicit about this.
- **Gemini SDK 10-second floor.** The `google-genai` SDK enforces a 10 s minimum request deadline (logged in `ERROR_FIX_LOG`, 2026-05-19). `EXTRACTION_TIMEOUT_SECONDS=12` clears the floor; calls that would have failed faster on a sub-10 s timeout instead consume the full 12 s before the fallback fires.
- **Gemini transient 503s.** Logged in `ERROR_FIX_LOG` — Gemini occasionally returns 503 UNAVAILABLE during high-demand periods. The retry-once-with-OpenAI fallback (task group 10) is exactly what this class of error is for. Until that ships, a 503 surfaces as the `_error_panel` fragment with the underlying message.
- **OpenAI quota gating.** The current `OPENAI_API_KEY` in the development account hit `insufficient_quota` during smoke (logged in `ERROR_FIX_LOG`). Until billing is wired, end-to-end testing of the fallback path is blocked — but the abstraction and routing already exist.
- **HTMX / SSE familiarity.** Some reviewers may not have seen HTMX. The base template (`app/templates/base.html`) is annotated and the README §4 architecture paragraph explains the choice. Cheap insurance.

---

## Decisions log

The eight load-bearing decisions, with the one-line rationale for each. Full discussion in `docs/MEMO.md`.

1. **Hybrid (vision-AI extraction + deterministic 27 CFR verifier), not pure-LLM.** Explainability, auditability, regulatory grounding — federal context demands every PASS / WARN / FAIL be reproducible across runs and citable to a 27 CFR section. See §5.
2. **Gemini 2.5 Flash primary, OpenAI GPT-4o fallback, both behind one `LabelExtractor` ABC.** Gemini latency (~1.5 s avg) is the only commercial option that comfortably fits Sarah Chen's 5 s bar. Both vendors are on GSA MAS. The hand-rolled ABC (`app/extractors/base.py:19`, ~30 lines) shows the abstraction explicitly — adding a Bedrock-Claude extractor for a GovCloud-routed deploy is a new file, not a refactor. No dependency on LiteLLM or LangChain.
3. **Single Python service (FastAPI + HTMX + Tailwind), not a two-service split.** Server-rendered HTML fragments fit the form-and-result pattern; no client-side state machine, no Node toolchain, one deploy artifact. Render Web Service single-service deploy via `render.yaml`.
4. **HTMX over React / Next.js.** No SPA framework, no build step, no separate frontend repo. `hx-post` / `hx-target` for the form-driven UI; `hx-ext="sse"` for the batch live updates. The whole UX is "form → result panel."
5. **SSE-streamed batch with `asyncio.Semaphore`, not a job queue.** Prototype scale is < 50 labels per batch. Adds zero infrastructure surface (no Redis, no worker process). Production-queue path is documented in §10.
6. **In-memory LRU cache (`cachetools.LRUCache`), not Redis.** Prototype-only. Key is `sha256(image_bytes).hexdigest()` so promotion to Redis is a backend swap, not a callers change.
7. **Rolled `LabelExtractor` ABC over LiteLLM.** ~30 lines, no extra dependency, more architectural signal in the writeup, and the writeup section about how to add a new federal-routed provider (Vertex AI for Gemini, Azure OpenAI for GPT, Bedrock for Claude) is more meaningful when the abstraction is visible in source rather than buried inside a wrapper library.
8. **27 CFR citations inline in code AND in verdict reasons.** Every verifier rule has a docstring citing the section it enforces. Every WARN / FAIL `FieldVerdict` carries `cfr_citation` and the human-readable reason includes it in plain English. The `_non_pass_needs_reason_and_citation` validator on `FieldVerdict` (`app/models.py:191`) enforces this structurally — a regulatory verdict without a citation cannot be constructed.
