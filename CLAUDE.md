# TTB Label Verification — Claude Code Guardrails

Project-specific operating rules for AI coding agents (Claude Code, etc.). Read every session before starting work. The full project context lives in `docs/`:

- [PRD](docs/PRD.md) — product requirements + MVP / stretch IDs
- [TASK_LIST](docs/TASK_LIST.md) — phased build plan
- [TECH_STACK](docs/TECH_STACK.md) — locked technology decisions + env vars + API surface
- [MEMO](docs/MEMO.md) — architecture rationale
- [USER_FLOW](docs/USER_FLOW.md) — end-user journeys + example queries
- [TESTING_STRATEGY](docs/TESTING_STRATEGY.md) — what we test and how
- [ERROR_FIX_LOG](docs/ERROR_FIX_LOG.md) — running log of resolved issues
- `TTB_Label_Verification_Presearch.md` — the original spec (source of truth for design)

## Environment Protection

- Never modify `.env` without explicit user confirmation. Treat it as user-owned.
- Never commit `.env` files. `.gitignore` covers `.env`, `.env.local`, `.env.*.local`; verify before any commit that touches the repo root.
- Never display API key values (`GEMINI_API_KEY`, `OPENAI_API_KEY`) in logs, in code, or in chat output. Redact in any new log middleware.
- Never hardcode secrets. New env vars go through `app/config.py` (Pydantic Settings) and are added to BOTH `.env.example` AND the `Environment Variables` section of [TECH_STACK](docs/TECH_STACK.md).

## Error Logging

Log to [ERROR_FIX_LOG](docs/ERROR_FIX_LOG.md) when any of the following take more than ~ 5 minutes to diagnose:

- Build failures (dependency resolution, lockfile drift, Docker build failures)
- Runtime errors from FastAPI, the extractors, or the verifier
- Vendor API errors (Gemini / OpenAI rate limits, schema drift, timeout patterns)
- Cache, HTMX, or SSE behavioral surprises
- Deployment failures on Render
- Eval-suite correctness issues (false PASS / false FAIL on the test set)
- Anything cross-cutting that another contributor would want to know about

Follow the template at the top of [ERROR_FIX_LOG](docs/ERROR_FIX_LOG.md) (Date, Error, Context, Root Cause, Fix, Prevention) and use the category prefixes listed there.

Do **NOT** log: typos, linter warnings, expected pre-implementation test failures, trivial syntax errors.

## Tech Stack Lock

The technology decisions below are locked from the presearch. Do not switch any of them without explicit user approval. New dependencies require a one-paragraph justification in the PR / commit message.

| Layer | Locked choice | Do NOT switch to |
|---|---|---|
| Language | Python 3.11+ | Node / TypeScript / Go for the service runtime |
| Web framework | FastAPI | Flask, Django, Starlette-only, aiohttp |
| Templates | Jinja2 (FastAPI default) | Mako, Chameleon, frontend-rendered React |
| Frontend interactivity | HTMX (CDN) | React, Vue, Svelte, Next.js, any SPA framework |
| Styling | Tailwind CSS via Play CDN | Bootstrap, MUI, a separate CSS build pipeline at prototype stage |
| Client-side state | Alpine.js (CDN, ~30 lines total use) | jQuery, vanilla DOM event spaghetti, a state-management library |
| Vision (primary) | Google Gemini 2.5 Flash via `google-genai` | Claude vision (latency), Bedrock (until prod path); also do NOT route through LiteLLM |
| Vision (fallback) | OpenAI GPT-4o via `openai` | A generic LLM gateway library |
| Extractor abstraction | Custom `LabelExtractor` ABC (`app/extractors/base.py`) | LiteLLM, LangChain LLM wrappers, instructor adapters — the writeup specifically calls out the rolled abstraction |
| Fuzzy matching | `rapidfuzz` (`token_sort_ratio`) | `fuzzywuzzy` (slow), `difflib` (less accurate at scale) |
| Schema | Pydantic v2 | dataclasses, attrs, marshmallow |
| Config | `pydantic-settings` reading `.env` | `python-dotenv` direct reads, env-var grep in code, hardcoded values |
| Cache | `cachetools.LRUCache` keyed by `sha256(image_bytes)` | Redis, Memcached, filesystem cache (prototype only — production path documented) |
| HTTP client | `httpx` | `requests` (sync only), urllib |
| Testing | `pytest` for verifier rules | `unittest`, ad-hoc test scripts |
| Hosting | Render single-service web | Two-service split, Vercel-functions-only, Kubernetes for prototype |
| Persistence | None (in-memory only) | Postgres, SQLite, filesystem writes of label data (PII concern) |
| Background work | `asyncio.Semaphore`-bounded in-process | Celery, RQ, arq (production path documented in MEMO) |

### Hard rules derived from the locked stack

- The `LabelExtractor` ABC stays at ~ 30 lines and stays pure-Python. New providers go through `app/extractors/<name>.py` with a concrete subclass — never a generic adapter.
- Every verifier rule in `app/verifier/rules.py` (and any new ones) must have a docstring citing the relevant 27 CFR section, and every FAIL / WARN reason string must include that citation in plain English.
- The canonical government warning text lives in exactly one place (`app/verifier/warning.py`) sourced verbatim from 27 CFR 16.21. Do not duplicate it elsewhere.
- The batch flow stays SSE + in-process semaphore. Do not introduce a queue, broker, or worker process for the prototype.
- The cache is in-memory only. Do not add a persistence layer "just in case."
- "ABV" abbreviation rejection lives in the verifier, not in the prompt. Ask the model only to extract the alcohol-content text verbatim.

### Adding new functionality

- **New required env var:** add to `app/config.py` + `.env.example` + the [TECH_STACK](docs/TECH_STACK.md) "Environment Variables" section, in that order.
- **New route:** document in [TECH_STACK](docs/TECH_STACK.md) "API Endpoints Summary" and (if user-visible) in [USER_FLOW](docs/USER_FLOW.md).
- **New verifier rule:** docstring with CFR citation; unit test in `tests/test_rules.py`; coverage matrix entry in [TESTING_STRATEGY](docs/TESTING_STRATEGY.md) if it satisfies an MVP / STR ID; FAIL / WARN reasons must include the citation.
- **New extractor provider:** subclass `LabelExtractor`; register in `app/extractors/__init__.py`; document in [TECH_STACK](docs/TECH_STACK.md); ensure the per-field-confidence JSON shape matches the existing prompt contract so the verifier remains provider-agnostic.
