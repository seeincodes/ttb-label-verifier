# TTB Label Verification — Technology Stack

## Architecture Overview

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

Batch flow adds a `/batch/stream/{run_id}` SSE endpoint that yields one HTML fragment per label as each completes, with `asyncio.Semaphore(BATCH_CONCURRENCY)` enforcing parallelism.

## Stack Decisions

| Layer | Technology | Version | Rationale |
|---|---|---|---|
| Language | Python | 3.11+ | Async-native, ecosystem fit for AI SDKs |
| Web framework | FastAPI | ^0.115 | Async, auto OpenAPI, fits HTMX HTML-fragment pattern |
| ASGI server | Uvicorn | ^0.30 | Standard FastAPI runner |
| Templates | Jinja2 | ^3.1 | FastAPI default; clean HTML-fragment rendering for HTMX |
| Frontend interactivity | HTMX (CDN) | 1.9.x | `hx-post` / `hx-target` / `hx-ext="sse"`; no JS framework or build step |
| Visual styling | Tailwind CSS (Play CDN) | latest | One `<script>` tag, zero toolchain for prototype; production would use the CLI build |
| Client-side state | Alpine.js (CDN) | 3.x | ~5 KB; used only for dropzone, image preview, small toggles (~30 lines total) |
| Vision (primary) | Google Gemini 2.5 Flash via `google-generativeai` | latest SDK | ~1.5 s avg latency (fits the 5 s bar); ~$1.67 / 10k pages; GSA MAS-listed; Vertex AI has FedRAMP / IL4 path |
| Vision (fallback) | OpenAI GPT-4o via `openai` | latest SDK | Strongest OCR on degraded images; GSA MAS-listed; Azure OpenAI has FedRAMP High |
| Fuzzy matching | rapidfuzz | ^3.9 | `token_sort_ratio`; battle-tested, fast |
| Schema | Pydantic | ^2.7 | Type-safe models for `ApplicationData`, `LabelData`, `VerificationResult` |
| Config | pydantic-settings | ^2.4 | Reads `.env`; type-safe settings |
| Cache | cachetools (`LRUCache`) | ^5.4 | In-memory, `maxsize=128`, keyed by `sha256(image_bytes)`; ~10 lines |
| HTTP client | httpx | ^0.27 | Async client where the vendor SDKs need one |
| File uploads | python-multipart | ^0.0.9 | FastAPI multipart-form support |
| Testing | pytest | ^8.3 | Unit tests for the verifier; eval suite is the integration test (manual run) |
| Hosting | Render (Web Service) | — | Single-service deploy; Fly.io is the documented alternative |
| Persistence | None (in-memory only) | — | Avoids PII concerns; documented as a prototype constraint |
| Eval harness | Custom Python (no framework) | — | ~20 test labels in `eval/test_set/`; results to `eval/results/` |

## Key Dependencies

### Backend (Python)

- `fastapi` — web framework, async route handlers, auto OpenAPI
- `uvicorn[standard]` — ASGI server with websocket / http2 extras
- `jinja2` — HTML templating for HTMX fragments
- `python-multipart` — multipart upload parsing
- `pydantic` — request / response schemas (`ApplicationData`, `LabelData`, `VerificationResult`)
- `pydantic-settings` — `.env`-driven config
- `google-generativeai` — Gemini 2.5 Flash extractor
- `openai` — GPT-4o fallback extractor
- `httpx` — async HTTP client where needed
- `rapidfuzz` — fuzzy string matching (`token_sort_ratio`)
- `cachetools` — `LRUCache` keyed by image hash
- `pytest` — unit tests for the verifier (the eval suite runs as a separate CLI tool)

### Frontend (CDN, no build step)

- HTMX 1.9.x — form-driven HTML-fragment swaps + SSE extension
- Alpine.js 3.x — dropzone, image preview, small toggles
- Tailwind CSS — utility-first styling via Play CDN

### Notably NOT used (and why)

- **LiteLLM / generic LLM router** — rolling our own `LabelExtractor` ABC (~30 lines) is more architectural signal for the writeup; no extra dependency.
- **React / Next.js / Vite** — single Python service is faster to deploy and avoids a second toolchain; HTMX is sufficient for the form-and-result pattern.
- **Database (Postgres / SQLite)** — no persistence by default; PII concerns and prototype scope.
- **Redis / external cache** — in-memory LRU only for the prototype; production path documented.
- **Background job queue (Celery / RQ / arq)** — SSE-streamed concurrent extraction is sufficient for prototype scale (< 50 labels per batch); production path documented.
- **`fuzzywuzzy`** — `rapidfuzz` is faster, more accurate, and MIT-licensed.

## Environment Variables

All env vars are read by `app/config.py` via `pydantic-settings`. `.env.example` mirrors this list with empty values so it can be copied to `.env` as the starting point.

```bash
# === Vision Model Configuration ===
EXTRACTOR_PROVIDER=gemini            # gemini | openai
GEMINI_API_KEY=                      # required if EXTRACTOR_PROVIDER=gemini or fallback used
GEMINI_MODEL=gemini-2.5-flash
OPENAI_API_KEY=                      # required if EXTRACTOR_PROVIDER=openai or fallback used
OPENAI_MODEL=gpt-4o

# === Timeouts & Performance ===
EXTRACTION_TIMEOUT_SECONDS=8         # per-call timeout; triggers fallback if exceeded
BATCH_CONCURRENCY=5                  # max simultaneous extractions in batch flow
CACHE_MAXSIZE=128                    # LRU cache entries (keyed by image SHA-256)

# === Application ===
APP_ENV=development                  # development | production
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8000                            # Render injects $PORT; honor it in production
```

## API Endpoints Summary

| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/` | Home (single-label form, sample-label buttons) | HTML |
| POST | `/verify` | Verify a single label (form data: image + expected fields) | HTML fragment (`_result_panel.html`) |
| POST | `/verify/json` | Verify a single label with structured JSON expected data | HTML fragment |
| GET | `/sample/{name}` | Pre-load a sample (`spirits-pass`, `abv-fail`, `warning-fail`) | HTML (form pre-filled, image attached) |
| GET | `/batch` | Batch upload page | HTML |
| POST | `/batch` | Accept batch files + optional CSV, return `run_id` | JSON `{run_id}` |
| GET | `/batch/stream/{run_id}` | SSE stream of per-label result fragments | `text/event-stream` |
| GET | `/batch/export/{run_id}.csv` | CSV export of completed batch | CSV |
| GET | `/health` | Liveness check (returns active extractor) | JSON |

Database schema section intentionally omitted — no persistence by default (see "Notably NOT used" above).
