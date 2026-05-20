# Error & Fix Log

Living log of errors encountered during development and how they were resolved. The goal is to avoid re-debugging the same class of problem and to surface project-specific gotchas for future contributors.

## Template

Each entry should follow this format:

```
### YYYY-MM-DD — [CATEGORY] One-line summary

- **Error:** exact error message or symptom (verbatim where possible)
- **Context:** what command was running / which file / what input triggered it
- **Root cause:** the actual underlying reason (not just "fixed by trying X")
- **Fix:** what was changed to resolve it (file + summary; link to commit if relevant)
- **Prevention:** what to look out for next time; pattern to avoid; test added; doc updated
```

Suggested category prefixes for this project:

- `[GEMINI]` — Gemini API / `google-generativeai` SDK errors
- `[OPENAI]` — OpenAI API / `openai` SDK errors
- `[EXTRACT]` — extraction prompt / JSON parsing / schema validation
- `[VERIFIER]` — rule logic, fuzzy matching, tolerances, normalization
- `[WARNING]` — government warning text or formatting check
- `[CFR]` — CFR citation or canonical text mismatch
- `[HTMX]` — HTMX form / fragment behavior
- `[SSE]` — server-sent events streaming, connection lifecycle
- `[CACHE]` — LRU cache / hash key issues
- `[FASTAPI]` — route, dependency, middleware, multipart upload
- `[CONFIG]` — `.env` / `pydantic-settings` problems
- `[DEPLOY]` — Render / Fly.io / Docker / env-var deployment issues
- `[EVAL]` — eval harness, metrics, test-set generation
- `[BUILD]` — `pyproject.toml` / `requirements.txt` / dependency resolution

## What to log

- Build failures (dependency conflicts, lockfile drift, Docker build failures)
- Runtime errors that took > 5 minutes to diagnose
- Vendor API errors (rate limits, schema changes, timeout patterns)
- Deployment failures (env vars missing, port binding, Render-specific quirks)
- Subtle correctness bugs (e.g. a fuzzy-matching threshold turned out wrong on real labels)
- Cross-extractor incompatibilities (Gemini's JSON shape differs subtly from OpenAI's)

## What NOT to log

- Typos
- Linter warnings
- Test failures that were expected (the test was written before the implementation)
- Trivial syntax errors caught in seconds

## Log

### 2026-05-19 — [GEMINI] Transient 503 UNAVAILABLE on `gemini-2.5-flash`

- **Error:** `google.genai.errors.ServerError: 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.'}}`
- **Context:** First end-to-end run of `scripts/smoke_extractor.py` (the new harness that drives `app.extractors.gemini.GeminiExtractor`) against the synthetic 32x32 PNG. Latency 1660 ms before the 503. Immediate retry succeeded at 6787 ms.
- **Root cause:** Gemini shared-infra overload. The 503 came from Google's side, not from a malformed request — the SDK call shape was correct, the timeout was 12 s (above the SDK's 10 s floor), and the second attempt with no changes succeeded.
- **Fix:** No code change. The extractor's existing `try/except Exception → ExtractorError(... from exc)` already wraps SDK errors cleanly. The fallback layer in task group 10 (Gemini timeout/5xx → retry once with OpenAI) is what this class of error is *for*.
- **Prevention:** Task group 10 implementation must (a) classify `ServerError` 503/502/504 as fallback-eligible (transient) vs. `ClientError` 400/401/403 as terminal (don't retry), and (b) keep the fallback at one retry — recursive fallback on a sustained Gemini outage would just inflate latency. The `ExtractorError.__cause__` chaining preserves the SDK status code for that classification.

### 2026-05-19 — [GEMINI] `google-genai` SDK enforces a 10s minimum deadline

- **Error:** `google.genai.errors.ClientError: 400 INVALID_ARGUMENT. {'error': {... 'message': 'Manually set deadline 8s is too short. Minimum allowed deadline is 10s.'}}`
- **Context:** `python scripts/smoke_gemini.py` after swapping from the deprecated `google-generativeai` SDK to the maintained `google-genai` SDK. The `HttpOptions(timeout=settings.extraction_timeout_seconds * 1000)` was passing 8000 ms, below the new SDK's 10s floor.
- **Root cause:** The new SDK enforces a minimum vendor-side request deadline of 10 seconds (returned as a 400 INVALID_ARGUMENT before the request leaves the SDK). The old `google-generativeai` SDK had no such floor, which is why our original `EXTRACTION_TIMEOUT_SECONDS=8` worked.
- **Fix:** Bumped `EXTRACTION_TIMEOUT_SECONDS` default from 8 → 12 across `.env`, `.env.example`, `app/config.py` (also tightened lower bound `ge=10`), `render.yaml`, and the references in `docs/TECH_STACK.md`, `docs/USER_FLOW.md`, `docs/MEMO.md`. 12 s comfortably clears the 10 s floor while still letting a stuck call trigger fallback rather than hanging indefinitely.
- **Prevention:** `Field(ge=10)` in `app/config.py` now refuses to load a Settings instance with a sub-10 s timeout, surfacing the constraint at boot rather than on the first API call.

### 2026-05-19 — [GEMINI] `google-generativeai` SDK is end-of-life (deprecation warning)

- **Error:** Smoke test prints `FutureWarning: All support for the google.generativeai package has ended. It will no longer be receiving updates or bug fixes.`
- **Context:** Initial smoke test of `scripts/smoke_gemini.py` against `google-generativeai==0.8.x`. The test succeeded (1.6 s latency, valid JSON) but the SDK is deprecated.
- **Root cause:** Google renamed and re-architected the Python SDK; the legacy package is frozen.
- **Fix:** Replaced `google-generativeai` with `google-genai` in `requirements.txt`. Rewrote `scripts/smoke_gemini.py` against the new `Client` API (`genai.Client(api_key=...)` + `client.models.generate_content(...)` + `types.Part.from_bytes(...)`). Updated docs/TECH_STACK.md, docs/CLAUDE.md, and the dep tables that referenced the old package name.
- **Prevention:** Going forward, all Gemini code uses `from google import genai` and `from google.genai import types`. The extractor implementation in task group 3 will be built on the new SDK, avoiding a second rewrite.

### 2026-05-19 — [OPENAI] Smoke test fails with 429 `insufficient_quota`

- **Error:** `openai.RateLimitError: Error code: 429 - {'error': {'message': 'You exceeded your current quota...', 'type': 'insufficient_quota', 'code': 'insufficient_quota'}}`
- **Context:** `python scripts/smoke_openai.py` against gpt-4o with a synthetic 32x32 PNG. The SDK call itself is shaped correctly; the failure is account-level, not code-level.
- **Root cause:** the `OPENAI_API_KEY` in `.env` is valid but the OpenAI account has no available quota / billing is not set up. OpenAI returns 429 with type `insufficient_quota` rather than 402, which is non-obvious.
- **Fix:** pending user action — either (a) add billing to the OpenAI account so the smoke test can complete, (b) get a different key, or (c) defer OpenAI work and rely on Gemini-only for now. The extractor abstraction (task group 3) and fallback logic (task group 10) still need an OpenAI implementation to satisfy [MVP7]; without a working key these will be untestable end-to-end.
- **Prevention:** smoke script now catches `RateLimitError` and exits with a clear "billing issue, not a code bug" message instead of a raw traceback. The eval harness and the production fallback path should distinguish "insufficient_quota" 429s (terminal) from rate-limit 429s (retryable).

## Common Issues to Watch For

Project-specific gotchas derived from the tech stack and the problem domain. Update this list as the build surfaces more.

### Vision-model integration

- **Gemini JSON-mode quirks** — `google-generativeai` JSON mode may return a string-wrapped JSON object even when `response_mime_type="application/json"` is set. Always `json.loads()` defensively and validate against the Pydantic model.
- **OpenAI vs. Gemini schema drift** — both providers can return slightly different shapes for the per-field confidence block. Keep the prompt identical across providers and rely on Pydantic to normalize; do not assume key parity.
- **Image payload size and MIME type** — Gemini and OpenAI both accept base64-encoded image bytes; pass the MIME type explicitly (`image/jpeg`, `image/png`). A truncated upload or missing MIME often manifests as a vague 400.
- **Vendor rate limits** — Gemini's free tier and OpenAI's lower tiers both have RPM limits that the batch flow can trip at `BATCH_CONCURRENCY=5`. Surface 429s in this log and consider lowering concurrency or adding exponential backoff before raising the cap.

### Verifier correctness

- **27 CFR 16.21 warning text** — the canonical text has subtle punctuation ("(1)", "(2)") and exact phrasing. Source verbatim from TTB / 27 CFR; do not transcribe by hand. Keep one copy in `app/verifier/warning.py`.
- **ABV tolerances cross beverage types** — ± 0.3 pp for spirits and malt (27 CFR 5.65 / 7.65); ± 1.5 pp for wine ≤ 14 % and ± 1.0 pp for wine > 14 % (27 CFR 4.36). Easy to swap by accident; the `tolerance_for(beverage, expected_abv)` lookup must cite the section in its docstring.
- **"ABV" abbreviation is forbidden** — the literal substring "ABV" on a label is a regulatory violation even though it is common shorthand colloquially. The check is on the extracted *label text*, not on the user-submitted application data.
- **Fuzzy-match thresholds (95 / 80)** — derived from Dave Morrison's "STONE'S THROW" / "Stone's Throw" example in the discovery interview. Tune against the eval suite, not against vibes.
- **Beverage-type conditionality** — class / type is required for spirits and wine, optional for malt and other. Country of origin is required only if `is_import=true`. Easy to enforce a field that is not required for the given beverage type.

### HTMX / SSE / FastAPI

- **HTMX fragment responses must NOT include the full HTML shell** — return only the swapped fragment, not the base template. Renders break silently when a `<html>` wrapper sneaks in.
- **SSE keep-alive** — Render's proxy can close idle connections; if a batch produces no output for > 60 s (e.g. all 5 concurrent slots are still mid-extraction), the connection drops. Emit periodic heartbeat comments (`": keep-alive\n\n"`) if you see drops.
- **FastAPI `UploadFile` is async** — always `await file.read()` and validate size BEFORE passing to the extractor; an unbounded multipart upload can OOM the worker.
- **Multipart file size limits** — Render's free tier caps request body size; document the limit in the UI ("max 10 MB per image") rather than failing with an opaque 413.

### Config and deployment

- **`.env` not loaded in Render** — Render injects env vars from the dashboard; `pydantic-settings` reads `os.environ` first. Confirm names match exactly between `.env`, `.env.example`, and the Render dashboard.
- **`PORT` injection** — Render sets `$PORT` at runtime; bind `uvicorn` to `--port $PORT` rather than a hardcoded `8000`.
- **API keys leaking into logs** — never log full request payloads if they could include the key in a header; redact in any log middleware.

### Eval

- **Generated test labels drift from real labels** — AI-generated test images are NOT a substitute for real label data; document this limitation explicitly in the eval section of the README.
- **Cost-per-label drift** — Gemini and OpenAI pricing changes over time; the README eval numbers should record the pricing snapshot used for the run.
- **Cache hits inflate latency stats** — the eval harness should distinguish cache-miss latency (the SLA the user cares about) from cache-hit latency (the bonus path).
