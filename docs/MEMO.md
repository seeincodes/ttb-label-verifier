# TTB Label Verification — Architecture Memo

## Project Summary

A federal-context prototype that combines vision AI (for reading messy label images) with deterministic Python rules grounded in 27 CFR (for enforcing regulatory checks). The output is a per-field verdict with citable reasoning that a TTB compliance agent — or a Treasury reviewer — can audit, reproduce, and trust. The design deliberately splits work along the line where AI is genuinely better than code (reading the world) and code is genuinely better than AI (deciding regulatory compliance).

## Key Architecture Decisions

### 1. Hybrid (vision-AI extraction + deterministic Python verification), not pure-LLM

**Decision:** The vision model produces structured JSON with per-field confidence. Deterministic Python rules then apply normalization, fuzzy matching, ABV tolerances per beverage type, regex exact-match for the warning text, and three discrete yes / no formatting checks. The model never returns "PASS" or "FAIL."

**Why over pure-LLM:** A compliance verdict is auditable only if it is reproducible. An LLM saying "this label fails" is unreviewable; a Python rule saying "ABV on label (45.31 %) exceeds expected (45.0 %) by 0.31 pp; tolerance per 27 CFR 5.65 is ± 0.3 pp" is reviewable, citable, and reproducible across runs. Federal context demands explainability — Treasury reviewers will weigh the *judgment* of where to draw the AI / deterministic line at least as heavily as the engineering. The README §5 ("Why I didn't just throw everything at the LLM") makes this case explicitly.

**Why over pure-deterministic:** Reading text off a glare-covered, angled, low-light bottle photo is exactly what vision models excel at and exactly what brittle OCR-then-regex pipelines fail at. The split is intentional: AI reads the world, Python rules decide compliance.

### 2. Gemini 2.5 Flash primary, OpenAI GPT-4o fallback — both behind one `LabelExtractor` ABC

**Decision:** Default extractor is Gemini 2.5 Flash. OpenAI GPT-4o is a second concrete implementation behind the same Python ABC. The active provider is selected via `EXTRACTOR_PROVIDER` env var. On Gemini error or timeout, the code falls back automatically to OpenAI and records `fallback_used: true` in the result for the audit panel.

**Why Gemini primary:** ~1.5 s average latency is the only commercial option that comfortably fits Sarah Chen's 5 s response bar. ~$1.67 / 10k pages is a defensible federal cost story. Gemini is on GSA MAS (added August 2025), and Vertex AI has the FedRAMP / IL4 path for an eventual production deploy.

**Why OpenAI fallback rather than Gemini-only:** GPT-4o has the strongest OCR accuracy on degraded images, which directly hits Jenny Park's "bad photos" pain point. It is also on GSA MAS, and Azure OpenAI has FedRAMP High. The fallback covers a Gemini outage / timeout, and it doubles as the A / B comparison in the eval suite.

**Why not Claude / Anthropic vision for this slot:** Latency on Claude's vision models does not comfortably fit the 5 s bar for this prototype workload. (Claude could be the right pick in a different architecture — e.g., heavier reasoning over already-extracted text — but not as the primary OCR step here.)

**Why a hand-rolled ABC rather than LiteLLM:** The ABC is ~30 lines. Showing the abstraction explicitly is much higher signal for the federal procurement story ("here is how you would add a Bedrock extractor for an agency on AWS GovCloud, or a Vertex IL4 extractor for one on Vertex") than importing a wrapper library. No vendor lock-in, no dependency we do not need, no library-version drift risk.

### 3. Single Python service (FastAPI + HTMX + Tailwind), not a two-service split

**Decision:** One FastAPI app serves both the HTML and the API. Jinja2 renders HTML fragments. HTMX swaps them. Alpine.js handles a couple of dropzone / preview interactions (~30 lines total). Tailwind via Play CDN handles styling. No separate frontend repo, no Node toolchain, no CORS plumbing.

**Why over React / Next.js + FastAPI:** The whole UX is "upload form → render result panel." There is no client-side state machine that would justify a SPA. Server-rendered HTML fragments are a perfect fit for this pattern. Deploy is one service, not two. Eval and writeup time is preserved for the things that actually win the interview (the CFR-grounded verifier, the eval suite, the writeup itself).

**Trade-off acknowledged:** HTMX may be unfamiliar to some reviewers. The README briefly notes "frontend uses HTMX (server-rendered HTML fragments) for simplicity" with a link to the docs. Cheap insurance.

### 4. SSE-streamed batch with in-process concurrency, not a background job queue

**Decision:** The batch endpoint accepts multiple files, returns a `run_id`, and a follow-up SSE endpoint streams one HTML fragment as each label completes. `asyncio.Semaphore(BATCH_CONCURRENCY)` (default 5) bounds simultaneous extractions.

**Why over a Celery / RQ queue:** Prototype scale is < 50 labels per batch. A queue adds Redis, a worker process, and a second deploy surface — none of which are needed at this scale, and all of which slow the build. SSE keeps the connection open so Jenny sees rows appear as they finish, not a multi-minute spinner. The production path to a real queue is documented in the README "What I'd do next in production" section.

**Why concurrency of 5:** Enough parallelism to make a 200-label batch demo visibly fast, but not enough to risk vendor rate limits or saturate the single Render web process.

### 5. In-memory LRU cache keyed by `sha256(image_bytes)`

**Decision:** `cachetools.LRUCache(maxsize=128)`. Cache key is the SHA-256 of the raw image bytes. Cache value is the full extraction JSON. ~10 lines of code.

**Why this matters for the demo:** A second pass on the same label returns in < 100 ms instead of ~ 1.5 s. This is a tangible "feels-fast" beat in a live demo — re-upload the same file and watch the latency drop.

**Why not Redis:** Prototype only. Production path (Redis or a content-addressed blob store) is documented in the README.

### 6. CFR citations inline in code AND in verdicts

**Decision:** Every verifier rule has a docstring citing the CFR section it enforces (27 CFR 5.65(b), 27 CFR 16.21, etc.). Every FAIL or WARN reason string includes the same citation in plain English. The README §6 has a single consolidated CFR-reference table.

**Why:** Treasury role. The whole point of the take-home is signaling that the candidate understands the regulatory context. Inline citations in code are far stronger evidence than a README claim of "I read the regs." A reviewer who opens any verifier file should immediately see this signal.

### 7. Per-field confidence with a "low confidence ⇒ ERROR" gate

**Decision:** The extraction prompt requires each field to return `{value, confidence}` where confidence is `high | medium | low`. The prompt explicitly instructs the model to return `null` plus `low` rather than guess. Any **required** field with `low` confidence produces verdict `ERROR` for that field and `ERROR` for the overall result.

**Why this matters:** A false PASS on a true violation is the worst-case failure for a compliance tool — it is silent and ships through. A false FAIL on a valid label is bad but recoverable (human reviews and corrects). Refusing to verdict-PASS-or-FAIL on a field the model could not read with confidence converts an undetectable false-positive risk into a visible "we could not read this; please reshoot or escalate" action. This is the AI-judgment-call the eval and the writeup will surface deliberately.

### 8. Verdict taxonomy: PASS, WARN, FAIL, ERROR (worst-of across fields)

**Decision:** Four verdicts. `PASS` is silent good. `WARN` is "needs human review, never auto-accepts" (used for fuzzy 80–94, ABV within 2× tolerance but outside 1× tolerance, marginal image quality). `FAIL` is "clear regulatory mismatch — cite the reg." `ERROR` is "could not reliably extract." Overall verdict = max(field verdicts) in severity order ERROR > FAIL > WARN > PASS.

**Why WARN is its own thing rather than "FAIL when in doubt":** The right human action differs. A FAIL probably becomes a rejection letter to the bottler; a WARN probably becomes "let me look at the image myself for ten seconds." Collapsing them costs the agent time and is exactly the over-eager-AI signal a Treasury reviewer is testing for.

## Processing Strategy

Lifecycle of one verification call:

1. **Receive** — `POST /verify` with image bytes + expected `ApplicationData`.
2. **Hash** — compute `sha256(image_bytes)`.
3. **Cache check** — if hit, skip to step 6 with the cached `LabelData`.
4. **Extract** — call the configured extractor (Gemini default). The extractor:
   - sends image + the per-field-confidence prompt
   - parses the response into `LabelData`
   - on timeout or 5xx, falls back to OpenAI and marks `fallback_used: true`
5. **Cache store** — write the `LabelData` to the LRU cache under the hash.
6. **Verify** — run the deterministic verifier:
   - apply beverage-type conditionality to the field list
   - run normalization (lowercase, punctuation strip, whitespace collapse, unit normalize, corporate-suffix strip)
   - run per-field rules (brand fuzzy, class fuzzy, ABV tolerance per CFR, net contents normalized exact, bottler fuzzy, country exact-if-import, warning text exact + formatting yes / no)
   - each rule returns a `FieldVerdict` with `verdict`, `reason`, `cfr_citation`, `comparison_method`, `evidence`
   - confidence gate: required field with `low` confidence ⇒ `ERROR`
7. **Aggregate** — overall verdict = worst across fields.
8. **Render** — Jinja2 fragment with verdict banner, per-field table, raw-JSON audit panel.
9. **Return** — HTML fragment for HTMX `hx-swap`.

Batch flow: same lifecycle per label, run inside an `asyncio.Semaphore(BATCH_CONCURRENCY)`-bounded task group; each completed task emits an SSE event carrying the corresponding `_batch_row.html` fragment.

## Known Failure Modes

| Failure mode | Likelihood | Mitigation |
|---|---|---|
| Gemini API latency variance pushes a label over the 5 s bar | Medium | `EXTRACTION_TIMEOUT_SECONDS=8`; automatic OpenAI fallback; SSE for batch so users see progress not a spinner; cache for repeat queries |
| Extraction prompt brittle across beverage types | High | Build the eval suite early; iterate the prompt against eval failures; include few-shot examples in the prompt; ship the eval numbers in README §9 with frank discussion of remaining failure modes |
| Government warning verbatim mismatch (multiple official variants) | Low (single canonical text confirmed from TTB.gov and 27 CFR 16.21) | Canonical text stored once in `app/verifier/warning.py` with the CFR citation in the docstring |
| Vision-model formatting check unreliable | Medium | Phrase as three discrete yes / no questions with explicit visual definitions (caps / bold / continuous); if the model returns `medium` confidence on formatting, downgrade FAIL → WARN to avoid over-rejection |
| Batch flow times out on very large uploads | Low at prototype scale | Concurrency limit of 5; SSE keeps the connection alive; production-queue path documented |
| False PASS on a true violation | Critical if it happens | Per-field confidence gate forces ERROR on `low`; eval suite tracks FP rate explicitly; WARN exists precisely so borderline cases surface to a human |
| False FAIL on a valid label | Bad but recoverable | Tolerances per CFR (not eyeballed); WARN band exists below FAIL; fuzzy 80–94 is WARN, not FAIL |
| Render free-tier cold start during demo | Low | Pre-warm before submission; document cold-start expectation in the README; screenshots + GIF as deploy-failure backup |
| Reviewer cannot access deployed URL (firewall, expired free tier) | Medium | Screenshots + 30 s screen-recording GIF in README; one-command Docker run as local backup |
| Disallowed "ABV" abbreviation slips through formatting check | Low | Explicit string check in `app/verifier/rules.py`; cites the appropriate 27 CFR section per beverage type |
| HTMX / SSE unfamiliar to the reviewer | Low | README briefly notes the stack choice and links to docs |
