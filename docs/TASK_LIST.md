# TTB Label Verification — Task List

Phased breakdown of the build, mapped to PRD requirement IDs. Time-budget reference: presearch §7 (~21 hours including buffer). See [PRD](PRD.md) for the [MVPx] / [STRx] / [DOCx] IDs each task group satisfies.

## Phase 1: MVP

### 1. Project setup [MVP14]

- [x] Create the repo layout from presearch §3.4 (`app/`, `tests/`, `eval/`, `sample_data/`).
- [x] `pyproject.toml` and `requirements.txt` with FastAPI, uvicorn[standard], jinja2, python-multipart, pydantic, pydantic-settings, rapidfuzz, cachetools, httpx, google-generativeai, openai, pytest.
- [x] `app/config.py` — `Settings` class via `pydantic-settings` reading from `.env`.
- [x] `.env.example` mirroring `.env` (no secret values).
- [x] `Makefile` with `dev`, `eval`, `test`, `deploy` targets.
- [x] FastAPI `/health` route + base Jinja2 template loading HTMX, Alpine.js, Tailwind via CDN.
- [x] Gemini API smoke test (one real call against a sample image, JSON back).
- [x] OpenAI API smoke test (same shape, same image).
- [x] Render service skeleton (`render.yaml` or service config) so deploy is wired before features land.

### 2. Pydantic schemas [MVP2] [MVP6] [MVP9] [MVP12]

- [x] `app/models.py` with `BeverageType` enum (`distilled_spirits`, `wine`, `malt_beverage`, `other`).
- [x] `ApplicationData` model per presearch §5.2 (with conditional `class_type`, `alcohol_content_pct`, `country_of_origin`).
- [x] `ExtractedField` generic with `value` + `confidence: Literal["high", "medium", "low"]`.
- [x] `LabelData` with one `ExtractedField` per checklist field plus the 3-part `government_warning_formatting` block.
- [x] `Verdict` enum (`PASS | WARN | FAIL | ERROR`) with severity ordering.
- [x] `FieldVerdict`: verdict, reason, `cfr_citation`, `comparison_method`, `evidence`.
- [x] `VerificationResult`: overall verdict, per-field verdicts, raw extraction JSON, `cache_hit`, `fallback_used`, `extractor_used`, `latency_ms`.

### 3. Extractor abstraction + Gemini implementation [MVP6] [MVP7] [MVP9]

- [x] `app/extractors/base.py` — `LabelExtractor` ABC with `extract(image_bytes: bytes, beverage_type: BeverageType) -> LabelData`.
- [ ] `app/extractors/gemini.py` — `GeminiExtractor` using `google-generativeai`, model from `GEMINI_MODEL`, timeout from `EXTRACTION_TIMEOUT_SECONDS`.
- [x] Per-field confidence prompt — required JSON shape per presearch §5.5.
- [x] Prompt instructs the model to return `null` + `"low"` rather than guess.
- [x] Three-part `government_warning_formatting` block (`caps_correct`, `bold_correct`, `continuous`, `confidence`) per presearch §5.1.
- [ ] Manual test on the 3 sample images from `sample_data/`.

### 4. Verifier — normalization, fuzzy matching, tolerances [MVP2] [MVP4] [MVP6] [MVP11] [MVP12]

- [ ] `app/verifier/normalize.py` — lowercase, strip punctuation, collapse whitespace; unit normalization (`750 mL` ↔ `0.75 L`); corporate-suffix stripping for bottler name (`LLC`, `Inc.`, `Co.`).
- [ ] `app/verifier/tolerances.py` — `tolerance_for(beverage, expected_abv)` returning the right tolerance per 27 CFR 5.65 / 7.65 / 4.36; cite the section in the docstring.
- [ ] `app/verifier/rules.py` — one function per field (brand, class / type, ABV, net contents, bottler, country, warning); each with a CFR-cited docstring; each returns a `FieldVerdict` with `verdict`, `reason`, `cfr_citation`, `comparison_method`, `evidence`.
- [ ] `app/verifier/warning.py` — canonical text constant from 27 CFR 16.21; two-layer check (text + formatting) per presearch §5.1; FAIL distinguishes text-mismatch from formatting-violation; cites 16.21 or 16.22 as appropriate.
- [ ] ABV-abbreviation check — fail if extracted alcohol-content text contains literal "ABV"; accept "Alc. by Vol.", "Alc./Vol.", "ALC. BY VOL.", with or without periods, with or without `%`; cite 27 CFR 5.65 / 7.65 / 4.36 by beverage.
- [ ] Beverage-type conditionality — verifier skips fields not required for the given beverage type per presearch §5.6.
- [ ] Pytest unit tests for each rule (`tests/test_rules.py`, `tests/test_tolerances.py`, `tests/test_warning.py`, `tests/test_normalize.py`).

### 5. Per-field confidence gate [MVP9]

- [ ] Any required field with `low` confidence ⇒ field verdict `ERROR`, overall verdict `ERROR`.
- [ ] ERROR reasons are actionable ("image too blurry to read class/type with confidence — please reshoot at a different angle").
- [ ] Optional fields at `low` confidence flag the field as unverifiable but do not bubble to overall ERROR.

### 6. Single-label UI [MVP1] [MVP4] [MVP5]

- [ ] `app/templates/base.html` — base layout, HTMX + Alpine.js + Tailwind CDN tags.
- [ ] `app/templates/index.html` — single-label upload form, beverage-type select, all 7 expected-data fields, "Try a sample label" buttons.
- [ ] Alpine.js image preview before submit.
- [ ] HTMX `hx-post` to `/verify` with `hx-target` swapping `_result_panel.html`.
- [ ] `_result_panel.html` — image thumbnail, colored verdict banner (PASS green / WARN yellow / FAIL red / ERROR gray), per-field table (extracted | expected | verdict | reasoning | CFR citation).
- [ ] Collapsible "view raw extraction" panel showing the raw JSON for audit.
- [ ] Three pre-loaded samples in `sample_data/`: clean spirits (PASS), ABV mismatch (FAIL), malformed warning (FAIL with 27 CFR 16.22 cite).

### 7. JSON upload path [MVP1]

- [ ] Accept structured JSON upload as an alternative to the form for expected data.
- [ ] Validate via `ApplicationData`; surface Pydantic validation errors in the UI.

### 8. Cache [MVP8] [MVP10]

- [ ] `app/cache.py` — `cachetools.LRUCache(maxsize=CACHE_MAXSIZE)`; key = `hashlib.sha256(image_bytes).hexdigest()`; value = the full `LabelData`.
- [ ] Measure cache-miss / cache-hit paths; ensure hit returns in < 100 ms.
- [ ] Surface `cache_hit` status in the result panel for the demo ("cached result — re-verified instantly").

### 9. Batch flow with SSE [MVP3]

- [ ] `app/templates/batch.html` — Alpine.js drag-and-drop dropzone, optional CSV upload for expected data (one row per filename).
- [ ] `POST /batch` accepts files + CSV, returns `{run_id}`.
- [ ] `GET /batch/stream/{run_id}` SSE endpoint yields `_batch_row.html` fragment per completed label plus `progress` events.
- [ ] Concurrency bounded by `asyncio.Semaphore(BATCH_CONCURRENCY)`.
- [ ] Filter chips (All / Failures / Warnings / OK) implemented client-side.
- [ ] `GET /batch/export/{run_id}.csv` — CSV export of results.

### 10. OpenAI fallback + model swap [MVP7]

- [ ] `app/extractors/openai.py` — `OpenAIExtractor` via the `openai` SDK using `OPENAI_MODEL`; same prompt + same JSON contract.
- [ ] Factory in `app/extractors/__init__.py` selects on `EXTRACTOR_PROVIDER`.
- [ ] Automatic fallback: on Gemini timeout / 5xx, retry once with OpenAI; result records `fallback_used: true` for the audit panel.
- [ ] Manual smoke test with `EXTRACTOR_PROVIDER=openai`.

### 11. Eval harness [MVP13]

- [ ] `eval/test_set/labels/` — ~ 20 images: 5 easy / 5 hard image quality / 5 violations / 5 edge cases (presearch §6.1).
- [ ] `eval/test_set/GENERATION.md` — generation prompts for any AI-generated test images, for reproducibility.
- [ ] `eval/test_set/expected/*.json` — expected `ApplicationData` and expected overall verdict per label.
- [ ] `eval/harness.py` — runs every label through extract + verify, computes per-field accuracy, FP / FN rates, verdict distribution, p50 / p95 / p99 latency, cost per label, cache hit rate.
- [ ] `make eval` invokes the harness; prints summary table; writes JSON to `eval/results/` (gitignored).
- [ ] Comparison run with `EXTRACTOR_PROVIDER=openai` to surface A / B trade-offs.

### 12. Deploy [MVP14]

- [ ] Render web service: env vars set (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `EXTRACTOR_PROVIDER`, etc.), single-service web deploy.
- [ ] End-to-end smoke test on the deployed URL.
- [ ] Screenshots + 30 s screen-recording GIF captured for the README deployment-failure backup.

## Phase 2: Polish

### 13. Error boundaries and loading states

- [ ] HTMX `hx-indicator` for loading state on Verify.
- [ ] Friendly error page for upload failures (file too large, wrong type).
- [ ] Timeout handling surfaced in the UI ("model timed out — retrying with fallback").
- [ ] Accessibility pass: large hit targets (Sarah's "73-year-old mother" constraint), keyboard focus order, color-not-only verdict signaling.

### 14. README writeup [DOC1] [DOC2] [DOC3] [DOC4]

- [ ] All 11 sections from presearch §8.
- [ ] §5 "Why I didn't just throw everything at the LLM."
- [ ] §6 single consolidated table of every 27 CFR section referenced.
- [ ] §8 stakeholder-signals table mapping every Sarah / Dave / Jenny / Marcus signal to a concrete design decision.
- [ ] §9 eval results — actual numbers, frank failure-mode discussion, Gemini-vs-OpenAI A / B comparison.
- [ ] §10 "What I'd do next in production" — PII, COLA integration, observability, retraining cadence, human-in-the-loop for WARN, background queue for batches > 50, GovCloud-pathed model.
- [ ] §11 trade-offs and limitations — PDF support, multi-page handling, persistent storage, auth.

## Phase 3: Final

### 15. Stretch features (if time permits)

- [ ] **[STR1]** Image-quality pre-check with actionable feedback.
- [ ] **[STR2]** Bounding-box overlay viewer.
- [ ] **[STR3]** A / B model comparison UI (Gemini + OpenAI side by side).
- [ ] **[STR4]** Keyboard shortcuts (Jenny's power-user case).
- [ ] **[STR5]** In-app eval dashboard.
- [ ] **[STR6]** Wine class-boundary edge case (14.5% wine labeled "table wine" — class designation FAIL even when numeric tolerance technically passes).

### 16. Submission

- [ ] Clean commit history check (meaningful messages, no `.env` committed, `.gitignore` covers `.env`, `eval/results/`, `__pycache__`, `.venv`).
- [ ] Every "Definition of done" box from presearch §11 checked.
- [ ] Repo set to public on GitHub.
- [ ] README polish pass — read aloud, fix awkward phrasing, confirm every section is present.
