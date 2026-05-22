# Design notes

The expanded background that used to live in the README. Audience: a reviewer who has already read the README and wants the full rubric-style evidence.

---

## 1. Why hybrid (vision AI + deterministic verifier), not pure-LLM

The single most-important architectural decision.

### 1.1 False-PASS risk is asymmetric

A compliance tool has two failure modes. A **false FAIL** on a valid label is recoverable — the agent eyeballs the label for ten seconds and overrides. A **false PASS** on a true violation is silent and ships through; the bottler walks away with an approval they should not have. The asymmetry means the verifier must err toward FAIL / WARN / ERROR on ambiguity, and *never* let an LLM hallucination produce a silent PASS.

A pure-LLM "look at this label and tell me if it's compliant" architecture cannot offer that guarantee. The model could be wrong in ways that are invisible (it returns PASS but missed that the warning is in title case). A deterministic verifier with explicit thresholds cannot.

`app/models.py` enforces this structurally: `VerificationResult.overall` is *derived* from `field_verdicts` via `Verdict.worst_of`, and a `model_validator` rejects any result where the asserted overall disagrees with that derivation. A buggy caller cannot assemble a silent false PASS.

### 1.2 Auditability and reproducibility are non-negotiable for federal context

A Treasury compliance decision must be reviewable by a second agent, a supervisor, or counsel. *"The model said FAIL"* is not reviewable. *"Field `bottler_name`: extracted 'Old Tom Distillery LLC' vs expected 'Old Tom Distillery'; after corporate-suffix strip, fuzzy_token_sort = 100; verdict PASS per 27 CFR 5.36"* is — every step is reproducible, the threshold is in source, and the citation points to a real regulation.

Every `FieldVerdict` carries `verdict`, `reason`, `cfr_citation`, `comparison_method`, and `evidence`. The `_non_pass_needs_reason_and_citation` validator refuses to construct a WARN / FAIL without a CFR citation — the regulatory grounding is enforced by the schema, not by convention.

### 1.3 CFR citations come from the verifier, not from the prompt

If we asked the model to "include the relevant 27 CFR section in your response," it would happily invent plausible-sounding section numbers. Hallucinated citations on a federal compliance tool would be much worse than no citations. Instead, the prompt asks the model only to extract verbatim label text and to answer three discrete yes/no formatting questions; the deterministic verifier owns every citation. The citation tables in `app/verifier/rules.py` and `app/verifier/tolerances.py` are the single source of truth.

---

## 2. The full 27 CFR citation table

Every CFR section that appears in source code, in a verifier docstring, or in a WARN/FAIL reason string. A reviewer can grep `27 CFR` across the repo and find every site listed here.

| CFR section | Governs | Where in the code |
|---|---|---|
| `27 CFR 4.21` | Wine class / type designation and standards of identity (e.g. "table wine" ≤14% ABV, "dessert wine" 14–24%) | `app/verifier/rules.py` (`_CLASS_TYPE_CITATIONS[WINE]` + `_wine_class_boundary_check` for the §4.21 class-vs-ABV consistency rule, STR6). |
| `27 CFR 4.32` | Wine brand name | `app/verifier/rules.py` (`_BRAND_CITATIONS[WINE]`). |
| `27 CFR 4.35` | Wine bottler / producer name | `app/verifier/rules.py` (`_BOTTLER_CITATIONS[WINE]`). |
| `27 CFR 4.36` | Wine ABV tolerances (±1.5 pp ≤14%, ±1.0 pp >14%) and abbreviation form | `app/verifier/tolerances.py`. |
| `27 CFR 4.37` | Wine net contents | `app/verifier/rules.py` (`_NET_CONTENTS_CITATIONS[WINE]`). |
| `27 CFR 4.39` | Wine country of origin | `app/verifier/rules.py` (`_COUNTRY_CITATIONS[WINE]`); also referenced in `app/models.py` import-validation error. |
| `27 CFR 5.32` | Spirits brand name | `app/verifier/rules.py`. |
| `27 CFR 5.35` | Spirits class / type | `app/verifier/rules.py`. |
| `27 CFR 5.36` | Spirits bottler / producer name | `app/verifier/rules.py`. |
| `27 CFR 5.36(d)` | Spirits country of origin | `app/verifier/rules.py`. |
| `27 CFR 5.38` | Spirits net contents | `app/verifier/rules.py`. |
| `27 CFR 5.65(b)` | Spirits ABV tolerance (±0.3 pp); ABV abbreviation prohibition | `app/verifier/tolerances.py`; FAIL reason in `app/verifier/rules.py`. |
| `27 CFR 5.66 / 4.39 / 7.66` | Import labels must declare country of origin | `app/models.py` `_import_country_consistency` validator. |
| `27 CFR 7.22` | Malt brand name and class | `app/verifier/rules.py`. |
| `27 CFR 7.25` | Malt bottler / producer name | `app/verifier/rules.py`. |
| `27 CFR 7.26` | Malt country of origin | `app/verifier/rules.py`. |
| `27 CFR 7.27` | Malt net contents | `app/verifier/rules.py`. |
| `27 CFR 7.65(c)` | Malt ABV tolerance (±0.3 pp) | `app/verifier/tolerances.py`. |
| `27 CFR 16.21` | Verbatim text of the government health warning | Canonical literal in `app/verifier/warning.py`; FAIL citation in same. The literal lives in exactly one place. |
| `27 CFR 16.22` | Formatting of the government warning (caps, bold, continuous, font size) | FAIL citation in `app/verifier/warning.py`. |
| `27 CFR Parts 4 / 5 / 7` | Top-level scope per beverage type | `app/models.py`; surfaced in the extraction prompt header. |

`(by analogy)` annotations on `BeverageType.OTHER` citations make explicit that seltzers / RTDs / cider ≥7% don't have a perfectly-matched CFR section — the verifier cites the closest analogous rule rather than fabricating coverage.

---

## 3. Stakeholder signals → design decisions

Every signal from the four discovery interviews (Sarah Chen, Dave Morrison, Jenny Park, Marcus Williams), mapped to a concrete decision in the code.

| Signal | Source | Concrete design decision in the code |
|---|---|---|
| **"≤5 s response per label or nobody uses it"** | Sarah Chen | Default extractor is Gemini 2.5 Flash (~1.5 s avg). LRU cache makes the second pass on the same image return in well under 100 ms. SSE in the batch flow shows progress, not a spinner. |
| **"My 73-year-old mother could figure it out"** | Sarah Chen | One primary action per screen. Form fits one viewport. Three preloaded sample buttons on the homepage so the reviewer clicks once and sees the full flow without uploading. Alpine.js image preview before submit. |
| **Batch upload of 200–300 labels** | Sarah Chen citing Janet (Seattle field office); reinforced by Jenny Park | First-class batch flow (`/batch`) with drag-and-drop dropzone, SSE-streamed per-row results, `asyncio.Semaphore(BATCH_CONCURRENCY=5)` concurrency limit, and CSV export. |
| **"STONE'S THROW" vs "Stone's Throw" must silently match** | Dave Morrison | `normalize_text` deletes apostrophes (not replaces with space) so possessives collapse to one token; `rapidfuzz.token_sort_ratio` is case- and word-order-insensitive; threshold 95 / 80 / <80 — calibrated to this exact example. |
| **"Old Tom Distillery LLC" vs "Old Tom Distillery" must match** | Dave Morrison | `strip_corporate_suffixes` runs *before* the fuzzy match for bottler-name comparison. |
| **Government warning verbatim text + formatting** | Jenny Park | Two-layer check: text content (canonical 27 CFR 16.21 literal in `app/verifier/warning.py`, compared after whitespace collapse and case-fold) and formatting (three vision-model yes/no questions citing 27 CFR 16.22). Both layers must pass. FAIL distinguishes which layer failed. |
| **Bad photos: angles, glare, low light** | Jenny Park | Vision-model OCR handles this natively. Per-field confidence wired through the verifier's confidence gate so a required field at `low` confidence becomes ERROR with an actionable reshoot reason — never a silent false PASS. Plus the STR1 client-side image-quality pre-check that rejects too-dark / too-bright / blank-wall photos before they reach the model. |
| **Network / firewall constraints; GSA MAS vendor lists** | Marcus Williams | Stateless calls to exactly two domains (`generativelanguage.googleapis.com` for Gemini, `api.openai.com` for OpenAI). Both vendors are on GSA MAS. |
| **PII / federal compliance for prod** | Marcus Williams | No persistence by default. LRU cache is in-memory only and wiped on restart. Production-deploy path (GovCloud-routed model traffic, PII redaction, retention controls) documented below. |
| **WARN is a different action than FAIL** | Implicit from Sarah / Jenny | `Verdict` enum is four-valued (PASS / WARN / FAIL / ERROR), severity-ordered ERROR > FAIL > WARN > PASS. WARN is 80–94 fuzzy or 1×–2× tolerance ABV — "human should glance at this," not auto-reject. |

---

## 4. What I'd do next in production

The prototype is consciously a prototype. The decisions below would change for a production deploy.

- **PII handling and retention.** Input redaction before logs, explicit retention windows on any persisted artifact, image-hash-only logs by default (`sha256` already in the cache key — extend to the audit trail), a data-handling appendix to the SORN.
- **COLA integration.** The current flow takes expected `ApplicationData` from a form or JSON upload. Production would fetch the application record from COLA directly via its API, eliminating data-entry as a source of false-FAILs.
- **Observability.** Structured logging (JSON, with request IDs), Prometheus / OpenTelemetry metrics on extractor latency / fallback rate / verdict distribution, alerts on FP-rate drift, and a dashboard for the eval suite.
- **Retraining / prompt-tuning cadence with an agent feedback loop.** Every WARN verdict the agent resolves becomes a labelled data point. A weekly batch run on the accumulated feedback surfaces drift and feeds a prompt-revision PR. The eval suite is the regression test.
- **Human-in-the-loop workflow for WARN.** WARN currently surfaces in the result panel. Production adds a queue view (assigned reviewer, time-to-decision SLA, decision audit log) — the WARN verdict is the entry point to a human workflow, not a terminal state.
- **Background queue for batches > 50.** SSE + `asyncio.Semaphore` is fine for 50-label batches on a single web process. Beyond that, batches go to a queue (`arq` or a managed service like SQS), with the SSE stream consumed by a separate result-streamer. The `LabelExtractor` ABC and the verifier are queue-agnostic — only the route layer changes.
- **GovCloud-routed model traffic.** Routing Gemini through Vertex AI (FedRAMP / IL4) instead of `generativelanguage.googleapis.com`, OpenAI through Azure OpenAI (FedRAMP High), plus a Bedrock-Claude extractor for agencies on AWS GovCloud. The `LabelExtractor` ABC is exactly the seam this lands at — a `BedrockClaudeExtractor` is a new file in `app/extractors/` plus one entry in the factory; no change to the verifier or the route handlers.
- **Cache promotion to Redis.** `LabelDataCache.key_for` already uses `sha256(image_bytes).hexdigest()` precisely so the cache can be swapped to Redis without changing callers. Same key, different backing store.
- **Font-size measurement on the warning.** A production version with a calibration reference could measure absolute font height to verify the 1 / 2 / 3 mm minimums in 27 CFR 16.22. The current prototype checks only caps / bold / continuous.
- **Authentication and authorization.** Agency SSO in front of the service. Per-user audit trail. Role-based access if approver / reviewer roles diverge.

---

## 5. Decisions log

The eight load-bearing architectural decisions, with the one-line rationale for each.

1. **Hybrid (vision-AI extraction + deterministic 27 CFR verifier), not pure-LLM.** Explainability, auditability, regulatory grounding. See §1.
2. **Gemini 2.5 Flash primary, OpenAI GPT-4o fallback, both behind one `LabelExtractor` ABC.** Gemini latency (~1.5 s avg) comfortably fits Sarah Chen's 5 s bar. Both vendors are on GSA MAS. The hand-rolled ABC (`app/extractors/base.py`, ~30 lines) shows the abstraction explicitly — adding a Bedrock-Claude extractor for a GovCloud-routed deploy is a new file, not a refactor.
3. **Single Python service (FastAPI + HTMX + Tailwind), not a two-service split.** Server-rendered HTML fragments fit the form-and-result pattern; no client-side state machine, no Node toolchain, one deploy artifact.
4. **HTMX over React / Next.js.** `hx-post` / `hx-target` for the form-driven UI; `hx-ext="sse"` for the batch live updates. The whole UX is "form → result panel."
5. **SSE-streamed batch with `asyncio.Semaphore`, not a job queue.** Prototype scale is < 50 labels per batch. Adds zero infrastructure surface. Production-queue path is in §4.
6. **In-memory LRU cache (`cachetools.LRUCache`), not Redis.** Prototype-only. Key is `sha256(image_bytes).hexdigest()` so promotion to Redis is a backend swap, not a callers change.
7. **Rolled `LabelExtractor` ABC over LiteLLM.** ~30 lines, no extra dependency, more architectural signal, and the production-path discussion about per-agency provider routing is more meaningful when the abstraction is visible in source rather than buried inside a wrapper library.
8. **27 CFR citations inline in code AND in verdict reasons.** Every verifier rule has a docstring citing the section it enforces. Every WARN / FAIL `FieldVerdict` carries `cfr_citation`. The `_non_pass_needs_reason_and_citation` validator on `FieldVerdict` enforces this structurally.
