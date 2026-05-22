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
- [x] `app/extractors/gemini.py` — `GeminiExtractor` using `google-genai`, model from `GEMINI_MODEL`, timeout from `EXTRACTION_TIMEOUT_SECONDS`.
- [x] Per-field confidence prompt — required JSON shape per presearch §5.5.
- [x] Prompt instructs the model to return `null` + `"low"` rather than guess.
- [x] Three-part `government_warning_formatting` block (`caps_correct`, `bold_correct`, `continuous`, `confidence`) per presearch §5.1.
- [x] Manual test on the 3 sample images from `sample_data/`. (Smoke harness `scripts/smoke_extractor.py` / `make smoke-extractor` proves the wiring end-to-end. Real-sample run is gated on task 6.7 producing the 3 sample images; re-run the same script against each then.)

### 4. Verifier — normalization, fuzzy matching, tolerances [MVP2] [MVP4] [MVP6] [MVP11] [MVP12]

- [x] `app/verifier/normalize.py` — lowercase, strip punctuation, collapse whitespace; unit normalization (`750 mL` ↔ `0.75 L`); corporate-suffix stripping for bottler name (`LLC`, `Inc.`, `Co.`).
- [x] `app/verifier/tolerances.py` — `tolerance_for(beverage, expected_abv)` returning the right tolerance per 27 CFR 5.65 / 7.65 / 4.36; cite the section in the docstring.
- [x] `app/verifier/rules.py` — one function per field (brand, class / type, ABV, net contents, bottler, country, warning); each with a CFR-cited docstring; each returns a `FieldVerdict` with `verdict`, `reason`, `cfr_citation`, `comparison_method`, `evidence`.
- [x] `app/verifier/warning.py` — canonical text constant from 27 CFR 16.21; two-layer check (text + formatting) per presearch §5.1; FAIL distinguishes text-mismatch from formatting-violation; cites 16.21 or 16.22 as appropriate.
- [x] ABV-abbreviation check — fail if extracted alcohol-content text contains literal "ABV"; accept "Alc. by Vol.", "Alc./Vol.", "ALC. BY VOL.", with or without periods, with or without `%`; cite 27 CFR 5.65 / 7.65 / 4.36 by beverage.
- [x] Beverage-type conditionality — verifier skips fields not required for the given beverage type per presearch §5.6.
- [x] Pytest unit tests for each rule (`tests/test_rules.py`, `tests/test_tolerances.py`, `tests/test_warning.py`, `tests/test_normalize.py`).

### 5. Per-field confidence gate [MVP9]

- [x] Any required field with `low` confidence ⇒ field verdict `ERROR`, overall verdict `ERROR`.
- [x] ERROR reasons are actionable ("image too blurry to read class/type with confidence — please reshoot at a different angle").
- [x] Optional fields at `low` confidence flag the field as unverifiable but do not bubble to overall ERROR.

### 6. Single-label UI [MVP1] [MVP4] [MVP5]

- [x] `app/templates/base.html` — base layout, HTMX + Alpine.js + Tailwind CDN tags.
- [x] `app/templates/index.html` — single-label upload form, beverage-type select, all 7 expected-data fields, "Try a sample label" buttons.
- [x] Alpine.js image preview before submit.
- [x] HTMX `hx-post` to `/verify` with `hx-target` swapping `_result_panel.html`.
- [x] `_result_panel.html` — image thumbnail, colored verdict banner (PASS green / WARN yellow / FAIL red / ERROR gray), per-field table (extracted | expected | verdict | reasoning | CFR citation).
- [x] Collapsible "view raw extraction" panel showing the raw JSON for audit.
- [x] Three pre-loaded samples in `sample_data/`: clean spirits (PASS), ABV mismatch (FAIL), malformed warning (FAIL with 27 CFR 16.22 cite).

### 7. JSON upload path [MVP1]

- [x] Accept structured JSON upload as an alternative to the form for expected data.
- [x] Validate via `ApplicationData`; surface Pydantic validation errors in the UI.

### 8. Cache [MVP8] [MVP10]

- [x] `app/cache.py` — `cachetools.LRUCache(maxsize=CACHE_MAXSIZE)`; key = `hashlib.sha256(image_bytes).hexdigest()`; value = the full `LabelData`.
- [x] Measure cache-miss / cache-hit paths; ensure hit returns in < 100 ms.
- [x] Surface `cache_hit` status in the result panel for the demo ("cached result — re-verified instantly").

### 9. Batch flow with SSE [MVP3]

- [x] `app/templates/batch.html` — Alpine.js drag-and-drop dropzone, optional CSV upload for expected data (one row per filename).
- [x] `POST /batch` accepts files + CSV, returns `{run_id}`.
- [x] `GET /batch/stream/{run_id}` SSE endpoint yields `_batch_row.html` fragment per completed label plus `progress` events.
- [x] Concurrency bounded by `asyncio.Semaphore(BATCH_CONCURRENCY)`.
- [x] Filter chips (All / Failures / Warnings / OK) implemented client-side.
- [x] `GET /batch/export/{run_id}.csv` — CSV export of results.

### 10. OpenAI fallback + model swap [MVP7]

- [x] `app/extractors/openai.py` — `OpenAIExtractor` via the `openai` SDK using `OPENAI_MODEL`; same prompt + same JSON contract.
- [x] Factory in `app/extractors/__init__.py` selects on `EXTRACTOR_PROVIDER`.
- [x] Automatic fallback: on Gemini timeout / 5xx, retry once with OpenAI; result records `fallback_used: true` for the audit panel.
- [x] Manual smoke test with `EXTRACTOR_PROVIDER=openai`. (Live: OpenAI 429 insufficient_quota → automatic fallback to Gemini succeeded; audit.fallback_used=True; see ERROR_FIX_LOG 2026-05-20.)

### 11. Eval harness [MVP13]

- [x] `eval/test_set/labels/` — ~ 20 images: 5 easy / 5 hard image quality / 5 violations / 5 edge cases (presearch §6.1). (Synthetic JSON fixtures, not real images — see GENERATION.md for the rationale.)
- [x] `eval/test_set/GENERATION.md` — generation prompts for any AI-generated test images, for reproducibility.
- [x] `eval/test_set/expected/*.json` — expected `ApplicationData` and expected overall verdict per label.
- [x] `eval/harness.py` — runs every label through extract + verify, computes per-field accuracy, FP / FN rates, verdict distribution, p50 / p95 / p99 latency, cost per label, cache hit rate.
- [x] `make eval` invokes the harness; prints summary table; writes JSON to `eval/results/` (gitignored).
- [x] Comparison run with `EXTRACTOR_PROVIDER=openai` to surface A / B trade-offs. (Documented in `eval/README.md` — fixture mode bypasses the extractor so the A/B is meaningful only in the future real-image mode; the harness has the switch.)

### 12. Deploy [MVP14]

- [ ] Render web service: env vars set (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `EXTRACTOR_PROVIDER`, etc.), single-service web deploy. <!-- pre-flight done (render.yaml verified, local production-style smoke passes); needs your Render dashboard. Checklist: `docs/DEPLOY.md`. -->
- [ ] End-to-end smoke test on the deployed URL. <!-- smoke commands prepared in docs/DEPLOY.md §4 -->
- [ ] Screenshots + 30 s screen-recording GIF captured for the README deployment-failure backup. <!-- capture path documented in docs/DEPLOY.md §5 -->

**Pre-flight (done locally — see `docs/DEPLOY.md`):**

- `render.yaml` reviewed: all env vars match `app/config.py` Settings; Render uvicorn start command verified locally; healthCheckPath `/health` returns 200.
- Production-style smoke (no `--reload`): `/`, `/health`, `/sample/{spirits-pass,abv-fail,warning-fail}`, `POST /batch` all return expected status codes and HTML content with the right CFR citations.
- Live extractor smoke: Gemini (6.8 s, §5.5 shape), OpenAI→Gemini fallback (7.1 s, audit.fallback_used=True).

## Phase 2: Polish

### 13. Error boundaries and loading states

- [x] HTMX `hx-indicator` for loading state on Verify.
- [x] Friendly error page for upload failures (file too large, wrong type).
- [x] Timeout handling surfaced in the UI ("model timed out — retrying with fallback"). (Already covered: `_run_verification`'s ExtractorError path renders `_error_panel`; `FallbackExtractor` surfaces both vendor failures when double-failure happens — pinned by test_both_failures_surface_in_error_message.)
- [x] Accessibility pass: large hit targets (Sarah's "73-year-old mother" constraint), keyboard focus order, color-not-only verdict signaling.

### 14. README writeup [DOC1] [DOC2] [DOC3] [DOC4]

- [x] All 11 sections from presearch §8.
- [x] §5 "Why I didn't just throw everything at the LLM."
- [x] §6 single consolidated table of every 27 CFR section referenced.
- [x] §8 stakeholder-signals table mapping every Sarah / Dave / Jenny / Marcus signal to a concrete design decision.
- [x] §9 eval results — actual numbers, frank failure-mode discussion, Gemini-vs-OpenAI A / B comparison.
- [x] §10 "What I'd do next in production" — PII, COLA integration, observability, retraining cadence, human-in-the-loop for WARN, background queue for batches > 50, GovCloud-pathed model.
- [x] §11 trade-offs and limitations — PDF support, multi-page handling, persistent storage, auth.

## Phase 3: Final

### 15. Stretch features (if time permits)

- [x] **[STR1]** Image-quality pre-check with actionable feedback. `app/image_quality.py` runs a classical-CV pre-check before the Gemini call (mean luminance + stddev thresholds catch lens-cap-dark, blown-out, and blank-wall photos); failure renders `_error_panel.html` with reshoot hints. Wired into `/verify` and `/extract`. 15 tests in `tests/test_image_quality{,_integration}.py`. All 11 manual_test labels pass; cache hits skip the gate (already-extracted images skip re-checking).
- [ ] **[STR2]** Bounding-box overlay viewer.
- [ ] **[STR3]** A / B model comparison UI (Gemini + OpenAI side by side).
- [ ] **[STR4]** Keyboard shortcuts (Jenny's power-user case).
- [x] **[STR5]** In-app eval dashboard. `GET /eval` reads the most recent `eval/results/eval-*.json` and renders headline metrics (FP/FN rate, verdict distribution, latency percentiles), per-field PASS rate, bucket breakdown, and a per-fixture table with drift highlighting (rows where actual != expected get an amber background and a "drift ✗" pill). Empty-state path renders `make eval` guidance instead of 404. Nav link added to base.html. 7 tests in `tests/test_eval_dashboard_route.py`.
- [x] **[STR6]** Wine class-boundary edge case (14.5% wine labeled "table wine" — class designation FAIL even when numeric tolerance technically passes). `check_class_type` runs the §4.21 class-vs-ABV consistency rule (`_wine_class_boundary_check`); covered by 9 tests in `tests/test_rules.py::TestWineClassBoundary` + 1 orchestrator test, plus eval fixture `edge_table_wine_above_14pct` (FAIL).

### 16. Submission

- [x] Clean commit history check (meaningful messages, no `.env` committed, `.gitignore` covers `.env`, `eval/results/`, `__pycache__`, `.venv`). (48 commits, 116 tracked files; `git ls-files` audit found zero leaked secrets/cache/venv.)
- [x] Every "Definition of done" box from presearch §11 checked. (Audit in `docs/DEFINITION_OF_DONE.md`: 11/13 ✅ in code, 2/13 🟡 require your dashboard.)
- [x] Repo set to public on GitHub. (Pushed 2026-05-21 to <https://github.com/seeincodes/ttb-label-verifier>, visibility=public.)
- [x] README polish pass — read aloud, fix awkward phrasing, confirm every section is present. (11 sections + Decisions log all present; one drifted `main.py:365` line ref swapped to a symbol reference.)
