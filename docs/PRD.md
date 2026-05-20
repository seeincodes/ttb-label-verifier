# TTB Label Verification — Product Requirements Document

## Overview

A prototype tool that helps TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance agents verify that an alcohol label image matches the data submitted in its application. Vision AI extracts label fields; deterministic Python rules grounded in 27 CFR compare them to expected values; a structured verdict with citable reasoning is returned per field and overall.

## Problem Statement

TTB compliance agents currently spend significant time visually cross-referencing label images against application data — checking brand names, class designations, ABV, government health warnings, formatting, and 27 CFR requirements. The work is slow, error-prone, and difficult to scale across hundreds of submissions per agent per week. A tool that automates the routine cases and surfaces only the genuinely ambiguous ones for human review would let agents spend more time on actual judgment calls and less on rote comparison.

This prototype is also the take-home for the AI Engineer / IT Specialist role at the US Department of the Treasury (TTB falls under Treasury). The evaluators will weigh AI judgment — prompt design, when to use AI vs. deterministic code, eval thinking, handling ambiguity, regulatory grounding — at least as heavily as backend craft. Inline 27 CFR citations are a deliberate signal that the candidate read the regs.

## Target Users

**Primary:** TTB compliance agents reviewing Certificate of Label Approval (COLA) submissions.

Key user personas surfaced in discovery interviews:

- **Sarah Chen** — wants results in ≤5s; "my 73-year-old mother could figure it out" UX; default flow must be dead-simple.
- **Dave Morrison** — moderate tech comfort; needs the cosmetic-match nuance ("STONE'S THROW" vs "Stone's Throw") to silently PASS without bothering him.
- **Jenny Park** — power user; uploads batches of 200–300 labels; needs batch flow, keyboard shortcuts, and accurate formatting checks on government warnings even from bad photos.
- **Janet (Seattle field office)** — batch uploader, similar pattern to Jenny.
- **Marcus Williams** — IT / security stakeholder; cares about firewall constraints, GSA-MAS vendor lists, PII handling, and FedRAMP path for any production deployment.

**Secondary:** Treasury reviewers evaluating the candidate's AI / SE judgment for the role this take-home is for.

## MVP Requirements

- [ ] **[MVP1]** Single-label verification flow — upload image (JPG / PNG) + expected data via form OR structured JSON; show image preview; one-click "Verify"; render colored verdict banner + per-field result table.
- [ ] **[MVP2]** TTB checklist field coverage — all seven fields verified: brand name, class / type designation, alcohol content (ABV), net contents, bottler name + address, country of origin (if import), government health warning.
- [ ] **[MVP3]** Batch upload with SSE live progress — drag-and-drop multiple files, optional CSV of expected data, per-row streaming as items complete, concurrency limit of 5, CSV export, filter (failures only / warnings only / all).
- [ ] **[MVP4]** Explainability with 27 CFR citations — every verdict includes extracted value, expected value, comparison method, confidence, and the specific CFR section it depends on; raw model response available in a collapsible audit panel.
- [ ] **[MVP5]** Pre-loaded sample labels — three samples on the homepage (clean spirits PASS, ABV-mismatch FAIL, malformed-warning FAIL); reviewer clicks once and sees the full flow without uploading anything.
- [ ] **[MVP6]** Hybrid architecture — vision AI extracts structured JSON with per-field confidence; deterministic Python verifier applies the rules, normalization, tolerances, regex, and formatting checks.
- [ ] **[MVP7]** Model abstraction — `LabelExtractor` ABC with Gemini and OpenAI implementations; swap default via `EXTRACTOR_PROVIDER` env var; automatic fallback to OpenAI on Gemini error or timeout.
- [ ] **[MVP8]** LRU cache — `cachetools.LRUCache` keyed by SHA-256 of image bytes, `maxsize=128`; cache-hit path returns in < 100 ms.
- [ ] **[MVP9]** Per-field confidence handling — extraction prompt requires per-field `high | medium | low` confidence; any required field at `low` confidence yields verdict **ERROR**, never a false PASS / FAIL.
- [ ] **[MVP10]** ≤ 5 s p95 response on cache miss — default model is Gemini 2.5 Flash; extraction is the only blocking step; verifier itself is sub-100 ms.
- [ ] **[MVP11]** Government warning two-layer check — (a) text-content exact match against the canonical 27 CFR 16.21 text after whitespace normalization, (b) three vision-model yes / no formatting questions per 27 CFR 16.22 (caps on "GOVERNMENT WARNING", bold weight, continuous statement); both must pass.
- [ ] **[MVP12]** Beverage-type conditionality — verifier honors which fields are required for `distilled_spirits` / `wine` / `malt_beverage` / `other`, per the matrix in presearch §5.6.
- [ ] **[MVP13]** Eval suite — ~ 20 labels covering 5 easy / 5 hard image quality / 5 violations / 5 edge cases; harness runnable via `make eval`; prints per-field accuracy, false-positive / negative rates, verdict distribution, p50 / p95 / p99 latency, cost per label, cache hit rate.
- [ ] **[MVP14]** Deployed working prototype — single-service deploy on Render (or Fly.io); end-to-end works on the public URL; env-vars configured for both Gemini and OpenAI.

## Final Submission Features

### Stretch features (if time permits)

- [ ] **[STR1]** Image-quality pre-check with actionable feedback ("photo is too dark — please reshoot").
- [ ] **[STR2]** Side-by-side image viewer with bounding-box overlays on detected fields.
- [ ] **[STR3]** A / B model comparison UI — Gemini and OpenAI side-by-side on the same label.
- [ ] **[STR4]** Keyboard shortcuts for power users (Jenny's case).
- [ ] **[STR5]** Eval dashboard accessible from the running app, not just the CLI.
- [ ] **[STR6]** Wine class-boundary edge case — 14.5% wine labeled "table wine" should FAIL on class designation even when ABV numeric tolerance technically passes.

### Writeup deliverables (gates the interview signal)

- [ ] **[DOC1]** README with the 11 sections from presearch §8 (especially §5 "Why I didn't just throw everything at the LLM", §6 CFR table, §8 stakeholder-signals table, §9 eval results, §10 production path).
- [ ] **[DOC2]** Stakeholder-signals table in README mapping every Sarah / Dave / Jenny / Marcus signal to a concrete design decision.
- [ ] **[DOC3]** Eval results section with actual numbers from final run + frank failure-mode discussion + Gemini-vs-OpenAI A / B comparison.
- [ ] **[DOC4]** Decisions log in README covering hybrid architecture, Gemini primary + OpenAI fallback, single Python service, HTMX, SSE, in-memory cache, rolled ABC over LiteLLM, inline CFR citations.

## Performance Targets

| Metric | Target | Source |
|---|---|---|
| Single-label latency (cache miss, p95) | ≤ 5 s | Sarah Chen interview |
| Single-label latency (cache hit) | < 100 ms | Definition of done |
| Batch concurrency | 5 simultaneous extractions | Presearch §3.3 |
| Default model latency (avg) | ~ 1.5 s (Gemini 2.5 Flash) | Presearch §3.2 |
| Cost per label | ~ $0.000167 (≈ $1.67 / 10k pages, Gemini 2.5 Flash baseline) | Presearch §3.2 |
| False-positive rate (PASS on a violation) | Track and minimize — critical metric for compliance | Eval §6.2 |
| False-negative rate (FAIL on a valid label) | Track and minimize | Eval §6.2 |
| Cache hit rate on second pass (same bytes) | ~ 100% | Presearch §5.8 |
| Eval test-set size | ~ 20 labels | Presearch §6.1 |
| Time budget (full build) | ~ 21 hours including buffer | Presearch §7 |

## Scope Boundaries

### In scope

- Single-label and batch verification flows.
- All seven TTB checklist fields with beverage-type conditionality (spirits / wine / malt / other).
- 27 CFR-grounded verdicts with inline citations.
- Two extractor implementations (Gemini, OpenAI) behind one ABC; env-var swap; automatic fallback.
- In-memory LRU cache.
- SSE batch streaming with concurrency limit of 5.
- Eval harness with ~ 20 generated test labels and reproducibility notes.
- Deployment on Render (or Fly.io).
- README with all 11 sections from presearch §8.

### Out of scope (documented in README "Trade-offs")

- COLA system integration (per Marcus — explicitly deferred).
- Authentication / user management.
- Persistent storage of labels (PII / federal compliance concerns; in-memory only).
- Production-grade observability (logging beyond prototype level).
- Multi-page PDF handling (single image only; PDF support noted as future work).
- Background job queue for batches > 50 labels (production path documented in the README).
- GovCloud / FedRAMP-High routed model traffic (writeup discusses the path: Bedrock for Claude, Vertex AI IL4 for Gemini, Azure OpenAI for GPT).
- Real human-in-the-loop workflow for WARN verdicts (writeup only).
- A / B feedback loop / retraining cadence (writeup only).
