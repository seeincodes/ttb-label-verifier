# TTB Label Verification — Testing Strategy

## Testing Pyramid

This prototype's correctness story is **deterministic-verifier unit tests + AI-extractor eval harness**, not a traditional pyramid. The AI extractor is exercised through the eval suite (real labels, scored end-to-end), not via mocked unit tests — mocking a vision model would test the mock, not the model.

```
        ┌──────────────────────┐
        │  Manual / smoke      │  ~ 5%  (deployed URL end-to-end, 3 sample buttons,
        │  on deployed URL     │         batch demo, cache-hit re-upload)
        ├──────────────────────┤
        │  Eval suite          │  ~ 25% (~ 20 labels, extract + verify integration)
        │  (real labels;       │
        │   `make eval`)       │
        ├──────────────────────┤
        │  pytest units on    │  ~ 70% (verifier rules, normalization, tolerances,
        │  the verifier        │         warning text + formatting, beverage-type
        │  (no API calls)     │         conditionality, confidence gate, cache)
        └──────────────────────┘
```

The extractor itself is treated as a third-party dependency — we do not unit-test Gemini or OpenAI; we measure them in the eval suite.

## Coverage Targets

| Layer | Target | Tool | Where measured |
|---|---|---|---|
| Verifier rules | 100% line + branch | `pytest` (optionally `pytest-cov`) | `tests/test_rules.py` |
| Normalization | 100% line | `pytest` | `tests/test_normalize.py` |
| Tolerances (ABV) | All four beverage paths + wine class boundary | `pytest` | `tests/test_tolerances.py` |
| Warning text + formatting | Both layers (text + three formatting Qs) | `pytest` | `tests/test_warning.py` |
| Per-field confidence gate | Required-low / required-medium / optional-low | `pytest` | `tests/test_rules.py` |
| Beverage-type conditionality | One test per (beverage × field) cell in §5.6 matrix | `pytest` | `tests/test_rules.py` |
| Cache | Miss / hit / SHA-256 keying / eviction | `pytest` | `tests/test_cache.py` |
| Extractor → verifier integration | ~ 20 real labels via eval harness | `eval/harness.py` | `eval/results/*.json` |
| Single-label HTTP flow | One happy-path test per route (stretch) | `httpx`-based FastAPI test | `tests/test_routes.py` (stretch) |
| Batch SSE flow | Manual via deployed URL | manual | demo + screenshots |

Pytest is the only required test runner. The eval harness is a CLI tool, not a pytest plugin — its purpose is to produce the numbers that go in README §9, not to gate CI.

## Test Categories

### Unit tests (pytest, no external calls)

- **Normalization** — lowercase + punctuation strip + whitespace collapse; unit conversion (`750 mL` ↔ `0.75 L`); corporate-suffix stripping (`LLC`, `Inc.`, `Co.`); idempotency.
- **Fuzzy-matching thresholds** — Dave's `STONE'S THROW` / `Stone's Throw` PASSes silently; `Old Tom Distillery LLC` / `Old Tom Distillery` PASSes after suffix strip; clearly different strings FAIL.
- **ABV tolerances** — one test per beverage type covering at-tolerance PASS, between-1×-and-2× WARN, beyond-2× FAIL, plus a wine class-boundary test (14.5% labeled "table wine" FAILs on class designation even when numeric tolerance technically passes).
- **Warning text check** — exact canonical text PASSes; subtle whitespace difference PASSes; one-word changed FAILs; numeric "(1)" / "(2)" markers preserved.
- **Warning formatting check** — given the 3-yes structure, all-yes PASSes; any-no FAILs with the 27 CFR 16.22 citation.
- **"ABV" abbreviation** — literal "ABV" on label FAILs with CFR cite; "Alc./Vol.", "Alc. by Vol.", with / without periods, with / without `%` — all PASS.
- **Per-field confidence gate** — required field at `low` ⇒ ERROR for that field; optional field at `low` ⇒ field marked unverifiable but does not bubble to overall ERROR; `medium` confidence does not trip the gate alone.
- **Beverage-type conditionality** — verifier skips class/type rule when `beverage_type=other`; verifier skips country-of-origin when `is_import=false`; all 7-field cases covered for spirits / wine / malt / other.
- **Cache** — `sha256(image_bytes)` keying is stable; cache hit returns the same `LabelData`; eviction beyond `maxsize` is LRU.

### Integration tests (the eval harness)

- **~ 20 labels** across 5 easy / 5 hard image quality / 5 violations / 5 edge cases (presearch §6.1).
- Each label has an expected `ApplicationData` JSON and an expected overall verdict + per-field verdict in `eval/test_set/expected/`.
- The harness produces per-field accuracy, false-positive rate (PASS on a violation — the critical metric), false-negative rate (FAIL on a valid), verdict distribution, p50 / p95 / p99 latency, cost per label, cache hit rate.
- Runs once with `EXTRACTOR_PROVIDER=gemini` and once with `EXTRACTOR_PROVIDER=openai` for the A / B comparison.
- Results land in `eval/results/<run-id>.json` (gitignored) plus a printed console summary.

### Manual / smoke tests (against the deployed URL)

- Each of the 3 sample buttons renders the expected verdict (spirits PASS / ABV FAIL / warning FAIL).
- A batch upload of 10 labels completes with SSE streaming and CSV export.
- Cache hit demoable: re-upload the same sample image and watch latency drop to < 100 ms.
- `/health` returns `{"status":"ok"}` with the expected extractor name.

## CI Integration

CI is intentionally light-weight for this prototype:

- **pytest** runs on every push (GitHub Actions, single Python 3.11 job): installs `requirements.txt`, runs `pytest -q`. Required to pass.
- **Eval suite is NOT in CI.** Running ~ 20 vision-model calls per push costs money and adds latency for no signal — the eval is run locally / manually before submission and its results are pasted into README §9 with the run date and pricing snapshot. The README is honest about this.
- **Linting / typing** are optional polish; if added, run as a separate non-blocking job to keep the green-tick story simple.
- **Deploy:** Render auto-deploys on push to `main`; no manual gate.

## Requirement Coverage Matrix

| Requirement | Verified by | Notes |
|---|---|---|
| [MVP1] Single-label flow | Manual smoke on deployed URL; (stretch) `tests/test_routes.py::test_verify_happy_path` | The HTTP route itself is thin; verifier covers the logic. |
| [MVP2] All 7 TTB checklist fields | `tests/test_rules.py` (one test per field) + eval suite | Per-field accuracy reported in eval. |
| [MVP3] Batch + SSE + concurrency limit + CSV export | Manual smoke on deployed URL (batch of 10) | SSE is hard to unit-test cleanly; manual is the source of truth here. |
| [MVP4] 27 CFR citations in verdicts | `tests/test_rules.py` asserts the `cfr_citation` field on every FAIL / WARN reason | Plus visual review of every rule's docstring. |
| [MVP5] Sample-label buttons | Manual smoke (3 buttons, 3 expected verdicts) | Eval suite also exercises these images. |
| [MVP6] Hybrid architecture | Eval suite (extractor returns JSON, verifier renders a verdict) | Tested as a whole through the eval. |
| [MVP7] Model abstraction + automatic fallback | Eval runs with both providers + `tests/test_factory.py` (stretch) for provider selection | Fallback path manually tested by killing Gemini's API key. |
| [MVP8] LRU cache | `tests/test_cache.py` (miss / hit / SHA-256 / eviction) + manual demo of latency drop | |
| [MVP9] Per-field confidence gate | `tests/test_rules.py::test_low_confidence_required_field_errors` + eval hard-image-quality labels | |
| [MVP10] ≤ 5 s p95 cache miss | Eval harness latency report | Reported as a single metric in README §9. |
| [MVP11] Warning two-layer check | `tests/test_warning.py` (text + formatting) + eval `warning-fail` sample | |
| [MVP12] Beverage-type conditionality | `tests/test_rules.py::test_beverage_type_conditionality_matrix` | Parametrized over the §5.6 matrix. |
| [MVP13] Eval suite via `make eval` | The eval suite itself is the test of [MVP13]; verify by running it. | README §9 includes the actual numbers and the run date. |
| [MVP14] Deployed prototype | Manual smoke on the deployed URL | Screenshots + GIF in README as deploy-failure backup. |
| [STR1] – [STR6] Stretch features | Manual smoke as each is added | Optional; documented in PRD even if not built. |
| [DOC1] – [DOC4] README + writeup | Manual review of README sections | Cross-checked against [TASK_LIST](TASK_LIST.md) Phase 2 task 14. |
