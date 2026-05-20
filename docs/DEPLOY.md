# Deploy checklist — Render

Step-by-step for the one-time Render setup of the TTB Label Verification
prototype. The repo includes `render.yaml` (Blueprint) — Render's
dashboard imports this directly. The only manual step is setting the two
secret env vars.

## Pre-flight (verified locally)

These all passed in the local production-style smoke (no `--reload`,
matches `render.yaml` start command):

- `GET /health` → 200 `{"status":"ok","extractor":"gemini"}` — matches `healthCheckPath`.
- `GET /` → 200 HTML with the upload form (`hx-post="/verify"`, `hx-indicator`, 3 sample buttons).
- `GET /sample/spirits-pass` → 200, verdict "PASS".
- `GET /sample/abv-fail` → 200, verdict "FAIL" with `27 CFR 5.65` citation.
- `GET /sample/warning-fail` → 200, verdict "FAIL" with `27 CFR 16.22` citation.
- `POST /batch` → 400 on empty file (graceful), 200 with `{run_id}` on valid upload.
- Live extractor smoke: `make smoke-extractor` returned a clean §5.5-shape JSON in 6.8 s.
- Live OpenAI→Gemini fallback smoke: `EXTRACTOR_PROVIDER=openai make smoke-extractor` recovered via the `FallbackExtractor` wrapper in 7.1 s with `audit.fallback_used=True`. See `docs/ERROR_FIX_LOG.md` 2026-05-20.

## Deploy steps

### 1. Connect the repo as a Render Blueprint

1. Push the latest `main` to the remote (already done via `git push origin main`).
2. Render dashboard → **New** → **Blueprint** → pick this repo. Render reads `render.yaml`, creates one Web Service named `ttb-label-verifier`.
3. Region: Oregon (matches `render.yaml`). Plan: Starter (~$7/mo; free tier is fine for the demo if Render still offers it on Web Services).

### 2. Set the two secrets in the dashboard

Render won't deploy until both secrets are set. From the service page → **Environment**:

| Env var | Source |
|---|---|
| `GEMINI_API_KEY` | <https://aistudio.google.com/app/apikey> (the same key in your local `.env`). |
| `OPENAI_API_KEY` | <https://platform.openai.com/api-keys>. If your account has `insufficient_quota` (the known issue per `docs/ERROR_FIX_LOG.md`), the primary path still works with `EXTRACTOR_PROVIDER=gemini`; the fallback wrapper will surface the OpenAI 429 in the audit log but Gemini's secondary slot won't fire because Gemini is *already* primary. Set the key anyway — if a Gemini outage hits, the fallback needs a valid key to retry against. |

The non-secret env vars (model names, timeouts, concurrency) are inlined in `render.yaml` and don't need dashboard action.

### 3. Trigger the first deploy

Render auto-deploys on the first save. Watch the build log:

- Expected build step: `pip install -r requirements.txt` → ~ 90 s.
- Expected runtime warmup: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` → first request ~ 200 ms after build completes.
- The `healthCheckPath: /health` line in `render.yaml` makes Render hold the deploy until `/health` returns 200. If you see "service is live" in the dashboard, the health endpoint is already up.

### 4. End-to-end smoke against the deployed URL

Replace `<URL>` with the Render-assigned URL (e.g. `https://ttb-label-verifier.onrender.com`):

```bash
URL=https://<your-app>.onrender.com

# 1. Health
curl -s -o /dev/null -w "/health → HTTP %{http_code}\n" $URL/health

# 2. Home page renders
curl -s $URL/ | grep -oE 'hx-post="/verify"|verify-spinner|spirits-pass'

# 3. All three sample routes
for name in spirits-pass abv-fail warning-fail; do
  echo "=== /sample/$name ==="
  curl -s -o /dev/null -w "HTTP %{http_code}\n" $URL/sample/$name
done

# 4. Real label verification (uses Gemini quota)
curl -s -F image=@sample_data/spirits-pass.png \
        -F beverage_type=distilled_spirits \
        -F brand_name="Old Tom Distillery" \
        -F class_type="Kentucky Straight Bourbon Whiskey" \
        -F alcohol_content_pct=45.0 \
        -F net_contents="750 mL" \
        -F bottler_name="Old Tom Distillery LLC" \
        -F bottler_address="123 Distillery Rd, Frankfort, KY" \
        -F is_import=false \
        $URL/verify | grep -oE 'PASS|FAIL|WARN|ERROR'
```

Expected: `HTTP 200` on every health/sample call; the `/verify` smoke should produce a real Gemini extraction (latency 1–3 s) and either PASS or ERROR depending on whether the placeholder PNG yields enough signal. (The synthetic blank PNG is *expected* to produce all-`null + low confidence` per the MVP9 prompt instruction — which renders as ERROR, not PASS. That's the right behavior; for a true demo you'd POST a real label image.)

### 5. Screenshots + GIF for the README

`README.md` §2 has TODO markers for screenshots / GIF. Capture path:

| Asset | What to capture | Why |
|---|---|---|
| `docs/screenshot-home.png` | The home page (`/`) showing the upload form and 3 sample buttons. | First impression for reviewers; shows the form is uncomplicated. |
| `docs/screenshot-pass.png` | `/sample/spirits-pass` result panel (verdict banner + per-field table + audit JSON `<details>`). | Demonstrates the §5.4 silent-PASS path + the audit-panel signal. |
| `docs/screenshot-fail.png` | `/sample/abv-fail` result panel — focus on the FAIL row's reasoning column showing the 3.5 pp delta + `27 CFR 5.65(b)` citation. | The inline-citation signal for the interview. |
| `docs/screenshot-warning.png` | `/sample/warning-fail` — the warning row with `27 CFR 16.22` citation. | The two-layer warning check signal. |
| `docs/demo.gif` | 30-s screen recording of: home → click sample button → result loads → expand audit panel. | Backup for reviewers behind a firewall who can't reach the deployed URL. |

Tools:
- macOS: `Cmd-Shift-5` for stills, **Kap** or **Gifox** for the GIF.
- Linux: GNOME Screenshot / `peek` for the GIF.
- Resize stills to ≤ 1200 px wide; GIF should be ≤ 5 MB to render inline on GitHub.

Once captured, update `README.md` §2:

```markdown
## §2 Demo

![Home page](docs/screenshot-home.png)
![PASS verdict](docs/screenshot-pass.png)
![FAIL verdict — ABV mismatch](docs/screenshot-fail.png)
![FAIL verdict — warning formatting](docs/screenshot-warning.png)

![30-second demo](docs/demo.gif)

Live: https://<your-app>.onrender.com
```

## Render quirks worth knowing

These are noted in `docs/ERROR_FIX_LOG.md` "Common Issues to Watch For":

- **`PORT` is dynamic.** Render injects `$PORT` at runtime; the start command in `render.yaml` already honors it (`--port $PORT`). Don't hardcode `8000`.
- **`.env` is not loaded in production.** Render injects env vars from the dashboard; `pydantic-settings` reads `os.environ` first, so dashboard vars override the local `.env`. Confirm names match exactly between `render.yaml`'s `envVars` block and `app/config.py`'s `Settings` fields.
- **SSE keep-alive.** Render's proxy can close idle SSE connections after ~60 s. The batch route already sets `X-Accel-Buffering: no` and `Cache-Control: no-cache`; if you see drops during long batches, add a heartbeat comment to `run_batch` (the entry point is `app/batch.py:run_batch`).
- **Free tier cold starts.** The starter / free Web Service spins down after inactivity. First request after a sleep is ~30 s. Reviewers who hit the URL once and don't see a response — that's the cold start, not a crash.
- **Multipart body cap.** Render's free tier caps request body around ~ 10 MB. The route already enforces `_MAX_IMAGE_BYTES = 10 MB` and renders the friendly `_error_panel` on a too-large upload (verified by `test_polish.py::test_too_large_returns_friendly_fragment`).

## After deploy

- Add the deployed URL to README §3 ("How to run" → "Hosted demo: <URL>").
- Open the deployed URL on a mobile browser too — the form is responsive but worth confirming the file input works on iOS/Android.
- Run `make eval` locally and commit the result to `eval/results/` (the dir is gitignored by default, so do this with `git add -f eval/results/<file>` if you want the README §9 numbers from a specific run to be reproducible).
