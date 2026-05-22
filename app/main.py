"""FastAPI entrypoint — single-label verification routes.

The extractor is injected via `Depends(get_extractor)` so tests can
override it without touching network. The verifier is pure-Python and
called inline.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from app.batch import (
    BatchStore,
    get_batch_store,
    parse_expected_csv,
    results_to_csv,
    run_batch,
)
from app.cache import LabelDataCache, get_cache
from app.config import get_settings
from app.dependencies import get_extractor
from app.extractors.base import LabelExtractor
from app.extractors.gemini import ExtractorError
from app.models import (
    ApplicationData,
    BeverageType,
    LabelData,
    VerificationResult,
    Verdict,
)
from app.verifier.rules import verify_label

BASE_DIR = Path(__file__).resolve().parent
SAMPLE_DIR = BASE_DIR.parent / "sample_data"
AVAILABLE_SAMPLES = ("spirits-pass", "abv-fail", "warning-fail")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(
    title="TTB Label Verification",
    description=(
        "Prototype that verifies alcohol-label images against application data "
        "using vision-AI extraction + a deterministic 27 CFR verifier."
    ),
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", response_class=JSONResponse)
async def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "extractor": settings.extractor_provider}


# ---------------------------------------------------------------------------
# Index — single-label upload form
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"extractor": settings.extractor_provider},
    )


# ---------------------------------------------------------------------------
# /verify — single-label verification
# ---------------------------------------------------------------------------


# UploadFile content-type → mime override map. Browsers usually send a
# correct type but we coerce to one we know vision SDKs accept.
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/jpg"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB — matches the UI hint


def _mime_from_upload(upload: UploadFile) -> str:
    """Map browser content-type → Gemini-compatible MIME, defaulting to JPEG."""
    ct = (upload.content_type or "").lower()
    if ct in _ALLOWED_MIME:
        return "image/jpeg" if ct == "image/jpg" else ct
    # Fall back on extension.
    name = (upload.filename or "").lower()
    if name.endswith(".png"):
        return "image/png"
    return "image/jpeg"


def _coerce_is_import(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"true", "1", "on", "yes"}


def _build_application_data(
    *,
    beverage_type: str,
    brand_name: str,
    class_type: str | None,
    alcohol_content_pct: str | None,
    net_contents: str,
    bottler_name: str,
    bottler_address: str,
    is_import: bool,
    country_of_origin: str | None,
) -> ApplicationData:
    """Convert raw form strings into a validated `ApplicationData`."""
    return ApplicationData(
        beverage_type=BeverageType(beverage_type),
        brand_name=brand_name,
        class_type=class_type or None,
        alcohol_content_pct=(
            float(alcohol_content_pct)
            if alcohol_content_pct and alcohol_content_pct.strip()
            else None
        ),
        net_contents=net_contents,
        bottler_name=bottler_name,
        bottler_address=bottler_address,
        is_import=is_import,
        country_of_origin=(country_of_origin or None) if is_import else None,
    )


def _extracted_display(label_data) -> dict[str, str]:
    """Per-field 'extracted' value text for the result table."""
    return {
        "brand_name": _show(label_data.brand_name.value),
        "class_type": _show(label_data.class_type.value),
        "alcohol_content": (
            f"{_show(label_data.alcohol_content_pct.value)} "
            f"({_show(label_data.alcohol_content_text.value)})"
        ),
        "net_contents": _show(label_data.net_contents.value),
        "bottler_name": _show(label_data.bottler_name.value),
        "bottler_address": _show(label_data.bottler_address.value),
        "country_of_origin": _show(label_data.country_of_origin.value),
        "government_warning": _show(label_data.government_warning_text.value),
    }


def _expected_display(app_data: ApplicationData) -> dict[str, str]:
    return {
        "brand_name": _show(app_data.brand_name),
        "class_type": _show(app_data.class_type),
        "alcohol_content": _show(app_data.alcohol_content_pct),
        "net_contents": _show(app_data.net_contents),
        "bottler_name": _show(app_data.bottler_name),
        "bottler_address": _show(app_data.bottler_address),
        "country_of_origin": _show(app_data.country_of_origin),
        "government_warning": "canonical 27 CFR 16.21 text",
    }


def _show(value) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


def _error_fragment(request: Request, heading: str, message: str) -> HTMLResponse:
    """Render the _error_panel fragment with HTTP 200 so HTMX swaps it in."""
    return templates.TemplateResponse(
        request=request,
        name="_error_panel.html",
        context={"heading": heading, "message": message},
        status_code=200,
    )


class _ImageUploadError(Exception):
    """Raised by `_read_image` for friendly-fragment-renderable failures.

    The route catches this and returns `_error_panel.html` so HTMX swaps a
    human-readable card into #result-panel rather than a raw JSON detail
    blob (the default behavior for HTTPException).
    """

    def __init__(self, heading: str, message: str) -> None:
        super().__init__(message)
        self.heading = heading
        self.message = message


async def _read_image(image: UploadFile) -> bytes:
    image_bytes = await image.read()
    if len(image_bytes) == 0:
        raise _ImageUploadError(
            heading="No image uploaded",
            message=(
                "The image field is empty. Please pick a JPG or PNG label "
                "photo and try again."
            ),
        )
    max_mb = _MAX_IMAGE_BYTES // (1024 * 1024)
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise _ImageUploadError(
            heading="Image too large",
            message=(
                f"This label image is {len(image_bytes) // (1024*1024)} MB, which "
                f"exceeds the {max_mb} MB upload limit. Compress or resize the "
                "image and try again."
            ),
        )
    return image_bytes


async def _run_verification(
    *,
    request: Request,
    image_bytes: bytes,
    mime: str,
    app_data: ApplicationData,
    extractor: LabelExtractor,
    cache: LabelDataCache,
) -> HTMLResponse:
    """Shared extract + verify + render path. /verify and /verify/json
    differ only in how they build `app_data`; everything downstream is
    identical.

    Cache strategy (MVP8): look up by sha256(image_bytes); on miss, call
    the extractor and populate the cache. We cache the *extraction*, not
    the verification result, so a re-verify with different expected data
    is sub-100 ms without re-paying for the model call.
    """
    settings = get_settings()
    cache_key = cache.key_for(image_bytes)
    cached = cache.get(cache_key)

    if cached is not None:
        label_data = cached
        latency_ms = 0
        cache_hit = True
        fallback_used = False
        provider_used = settings.extractor_provider
    else:
        started = time.perf_counter()
        try:
            label_data, audit = await extractor.extract_with_audit(
                image_bytes=image_bytes,
                beverage_type=app_data.beverage_type,
                mime_type=mime,
            )
        except ExtractorError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return _error_fragment(
                request,
                heading="Vision model could not read this label",
                message=(
                    f"{exc} (after {elapsed_ms} ms). Please try again in a moment, "
                    "or upload a sharper image."
                ),
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        cache.put(cache_key, label_data)
        cache_hit = False
        fallback_used = audit.fallback_used
        # provider_used surfaces "OpenAIExtractor" / "GeminiExtractor" — the
        # audit panel header is friendlier with the configured-provider
        # name, but on fallback we want to be honest about which one
        # actually produced the result.
        provider_used = (
            audit.provider_used if fallback_used else settings.extractor_provider
        )

    field_verdicts = verify_label(label_data, app_data)
    overall = Verdict.worst_of(fv.verdict for fv in field_verdicts.values())

    result = VerificationResult(
        overall=overall,
        field_verdicts=field_verdicts,
        raw_extraction=label_data,
        cache_hit=cache_hit,
        fallback_used=fallback_used,
        extractor_used=provider_used,
        latency_ms=latency_ms,
    )

    return templates.TemplateResponse(
        request=request,
        name="_result_panel.html",
        context={
            "result": result,
            "image_b64": base64.b64encode(image_bytes).decode("ascii"),
            "image_mime": mime,
            "extracted_display": _extracted_display(label_data),
            "expected_display": _expected_display(app_data),
            "raw_extraction_json": label_data.model_dump_json(indent=2),
        },
    )


@app.post("/verify", response_class=HTMLResponse)
async def verify(
    request: Request,
    image: UploadFile = File(...),
    beverage_type: str = Form(...),
    brand_name: str = Form(...),
    net_contents: str = Form(...),
    bottler_name: str = Form(...),
    bottler_address: str = Form(...),
    class_type: Optional[str] = Form(None),
    alcohol_content_pct: Optional[str] = Form(None),
    is_import: Optional[str] = Form(None),
    country_of_origin: Optional[str] = Form(None),
    extractor: LabelExtractor = Depends(get_extractor),
    cache: LabelDataCache = Depends(get_cache),
) -> HTMLResponse:
    try:
        image_bytes = await _read_image(image)
    except _ImageUploadError as exc:
        return _error_fragment(request, exc.heading, exc.message)

    try:
        app_data = _build_application_data(
            beverage_type=beverage_type,
            brand_name=brand_name,
            class_type=class_type,
            alcohol_content_pct=alcohol_content_pct,
            net_contents=net_contents,
            bottler_name=bottler_name,
            bottler_address=bottler_address,
            is_import=_coerce_is_import(is_import),
            country_of_origin=country_of_origin,
        )
    # ValidationError is a subclass of ValueError in pydantic v2, but
    # BeverageType("unknown") and float("abc") both raise plain ValueError
    # before Pydantic gets involved — catch the base.
    except ValueError as exc:
        return _error_fragment(request, "Invalid application data", str(exc))

    mime = _mime_from_upload(image)
    return await _run_verification(
        request=request,
        image_bytes=image_bytes,
        mime=mime,
        app_data=app_data,
        extractor=extractor,
        cache=cache,
    )


@app.post("/verify/json", response_class=HTMLResponse)
async def verify_json(
    request: Request,
    image: UploadFile = File(...),
    application_json: str = Form(...),
    extractor: LabelExtractor = Depends(get_extractor),
    cache: LabelDataCache = Depends(get_cache),
) -> HTMLResponse:
    """Verify a single label with the expected data supplied as JSON.

    The JSON body must validate against `ApplicationData`. The image is
    still a multipart upload. Same downstream flow as `/verify`; surface
    JSON-parse and Pydantic validation errors as a graceful _error_panel
    fragment rather than a raw 400/422 (HTMX would otherwise swap the
    JSON-detail blob into the result panel)."""
    try:
        image_bytes = await _read_image(image)
    except _ImageUploadError as exc:
        return _error_fragment(request, exc.heading, exc.message)

    try:
        payload = json.loads(application_json)
    except json.JSONDecodeError as exc:
        return _error_fragment(
            request,
            "Could not parse application JSON",
            f"JSON parse error: {exc.msg} (line {exc.lineno}, col {exc.colno}).",
        )

    try:
        app_data = ApplicationData.model_validate(payload)
    except ValueError as exc:
        return _error_fragment(
            request,
            "Application data failed validation",
            str(exc),
        )

    mime = _mime_from_upload(image)
    return await _run_verification(
        request=request,
        image_bytes=image_bytes,
        mime=mime,
        app_data=app_data,
        extractor=extractor,
        cache=cache,
    )


# ---------------------------------------------------------------------------
# /extract — upload-prefill flow. Image → JSON suggestions for the form.
# ---------------------------------------------------------------------------


def _prefill_payload(label: LabelData) -> dict:
    """Project a `LabelData` into the form-prefill JSON the frontend reads.

    Conventions:
      - Fields with confidence='low' OR value=null are omitted entirely.
        The form input stays empty so the agent fills it manually rather
        than starting from a likely-wrong guess (the MVP9 spirit applied
        to the prefill, not just the verifier).
      - `beverage_type` comes from the model's `beverage_type_guess` (the
        agent confirms via the dropdown).
      - `is_import` is derived: a country printed on the label ⇒ True.
        A domestic label (country=null) ⇒ False.
    """
    payload: dict = {}

    def _include(field_name: str, ef) -> None:
        if ef.value is None or ef.confidence == "low":
            return
        payload[field_name] = ef.value

    _include("brand_name", label.brand_name)
    _include("class_type", label.class_type)
    _include("alcohol_content_pct", label.alcohol_content_pct)
    _include("net_contents", label.net_contents)
    _include("bottler_name", label.bottler_name)
    _include("bottler_address", label.bottler_address)

    # is_import: derived from whether the model saw a country printed.
    has_country = (
        label.country_of_origin.value is not None
        and label.country_of_origin.confidence != "low"
    )
    payload["is_import"] = has_country
    if has_country:
        payload["country_of_origin"] = label.country_of_origin.value

    if label.beverage_type_guess is not None:
        payload["beverage_type"] = label.beverage_type_guess.value

    return payload


@app.post("/extract", response_class=JSONResponse)
async def extract_prefill(
    image: UploadFile = File(...),
    extractor: LabelExtractor = Depends(get_extractor),
    cache: LabelDataCache = Depends(get_cache),
) -> JSONResponse:
    """Run extraction on an uploaded image and return form-prefill suggestions.

    The verification flow still requires the agent to confirm / edit the
    pre-filled form before clicking Verify — the two-source comparison
    (label-vs-COLA) only has meaning if the COLA-side data is human-
    confirmed. This route just removes the typing.

    Uses the same extractor + cache as POST /verify, so a subsequent
    /verify on the same image bytes is a cache hit.
    """
    try:
        image_bytes = await _read_image(image)
    except _ImageUploadError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": exc.heading, "detail": exc.message},
        )

    cache_key = cache.key_for(image_bytes)
    cached = cache.get(cache_key)
    if cached is not None:
        return JSONResponse(_prefill_payload(cached))

    # Caller hasn't picked a beverage type yet — use a sensible default for
    # the prompt-conditioning; the model still returns its own beverage_type_guess.
    try:
        label_data = await extractor.extract(
            image_bytes=image_bytes,
            beverage_type=BeverageType.DISTILLED_SPIRITS,
            mime_type=_mime_from_upload(image),
        )
    except ExtractorError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": "Vision model could not read this label",
                "detail": str(exc),
            },
        )

    cache.put(cache_key, label_data)
    return JSONResponse(_prefill_payload(label_data))


# ---------------------------------------------------------------------------
# /sample/{name} — pre-canned demo, bypasses extractor
# ---------------------------------------------------------------------------


@app.get("/sample/{name}", response_class=HTMLResponse)
async def sample(request: Request, name: str) -> HTMLResponse:
    """Render a pre-canned sample so the demo runs offline.

    Loads sample_data/{name}.json (a `{label, application}` pair) plus
    sample_data/{name}.png (placeholder image), runs only the
    deterministic verifier, and renders the same `_result_panel.html`
    fragment inside a full page. No extractor call.
    """
    if name not in AVAILABLE_SAMPLES:
        raise HTTPException(status_code=404, detail=f"unknown sample: {name}")

    json_path = SAMPLE_DIR / f"{name}.json"
    png_path = SAMPLE_DIR / f"{name}.png"
    if not json_path.exists() or not png_path.exists():
        raise HTTPException(
            status_code=404, detail=f"sample assets missing for {name}"
        )

    payload = json.loads(json_path.read_text())
    label_data = LabelData.model_validate(payload["label"])
    app_data = ApplicationData.model_validate(payload["application"])

    field_verdicts = verify_label(label_data, app_data)
    overall = Verdict.worst_of(fv.verdict for fv in field_verdicts.values())

    result = VerificationResult(
        overall=overall,
        field_verdicts=field_verdicts,
        raw_extraction=label_data,
        cache_hit=False,
        fallback_used=False,
        extractor_used="sample (verifier-only, no model call)",
        latency_ms=0,
    )

    image_bytes = png_path.read_bytes()
    return templates.TemplateResponse(
        request=request,
        name="sample.html",
        context={
            "sample_name": name,
            "result": result,
            "image_b64": base64.b64encode(image_bytes).decode("ascii"),
            "image_mime": "image/png",
            "extracted_display": _extracted_display(label_data),
            "expected_display": _expected_display(app_data),
            "raw_extraction_json": label_data.model_dump_json(indent=2),
        },
    )


# ---------------------------------------------------------------------------
# /batch — multi-label upload, SSE-streamed results
# ---------------------------------------------------------------------------


@app.get("/batch", response_class=HTMLResponse)
async def batch_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request=request,
        name="batch.html",
        context={"batch_concurrency": settings.batch_concurrency},
    )


@app.post("/batch", response_class=JSONResponse)
async def batch_create(
    files: list[UploadFile] = File(...),
    expected_csv: str = Form(""),
    store: BatchStore = Depends(get_batch_store),
) -> JSONResponse:
    """Accept N image files + optional CSV; register a BatchRun and
    return its run_id. The actual extraction starts on the first
    GET /batch/stream/{run_id} request — keeps the POST snappy and
    avoids dropped work if the client never opens the stream."""
    if not files:
        raise HTTPException(status_code=400, detail="at least one file required")

    items = []
    for upload in files:
        data = await upload.read()
        if len(data) == 0:
            continue
        if len(data) > _MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{upload.filename}: image exceeds "
                    f"{_MAX_IMAGE_BYTES // (1024*1024)} MB limit"
                ),
            )
        items.append((upload.filename or "file", data, _mime_from_upload(upload)))

    if not items:
        raise HTTPException(status_code=400, detail="all uploaded files were empty")

    expected = parse_expected_csv(expected_csv) if expected_csv else {}
    run = store.create_run(items=items, expected=expected)
    return JSONResponse({"run_id": run.run_id, "total": len(items)})


@app.get("/batch/stream/{run_id}", response_class=StreamingResponse)
async def batch_stream(
    request: Request,
    run_id: str,
    extractor: LabelExtractor = Depends(get_extractor),
    cache: LabelDataCache = Depends(get_cache),
    store: BatchStore = Depends(get_batch_store),
) -> StreamingResponse:
    """Server-Sent Events stream of row + progress + done events.

    Heartbeat comment every loop tick defeats Render's idle-connection
    proxy timeout (ERROR_FIX_LOG note on SSE keep-alive).
    """
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")

    settings = get_settings()

    async def _event_stream():
        try:
            async for event in run_batch(
                run, extractor, cache, concurrency=settings.batch_concurrency
            ):
                payload = event["data"]
                # For row events, also embed a small HTML preview so a
                # curl consumer sees something human-readable. The Alpine
                # client reads the JSON only.
                if event["event"] == "row":
                    html_fragment = templates.get_template(
                        "_batch_row.html"
                    ).render(row=payload)
                    data = json.dumps(
                        {
                            "filename": payload["filename"],
                            "overall": payload["overall"],
                            "field_summary": payload["field_summary"],
                            "error": payload["error"],
                            "html": html_fragment,
                        }
                    )
                else:
                    data = json.dumps(payload)
                yield f"event: {event['event']}\ndata: {data}\n\n"
        except Exception as exc:  # noqa: BLE001 — surface on the stream
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx/render buffering
        },
    )


@app.get("/batch/export/{run_id}.csv")
async def batch_export(
    run_id: str,
    store: BatchStore = Depends(get_batch_store),
) -> PlainTextResponse:
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")

    csv_text = results_to_csv(run)
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="ttb-batch-{run_id}.csv"',
        },
    )
