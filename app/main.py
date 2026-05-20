"""FastAPI entrypoint — single-label verification routes.

The extractor is injected via `Depends(get_extractor)` so tests can
override it without touching network. The verifier is pure-Python and
called inline.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.config import get_settings
from app.dependencies import get_extractor
from app.extractors.base import LabelExtractor
from app.extractors.gemini import ExtractorError
from app.models import (
    ApplicationData,
    BeverageType,
    VerificationResult,
    Verdict,
)
from app.verifier.rules import verify_label

BASE_DIR = Path(__file__).resolve().parent
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
) -> HTMLResponse:
    image_bytes = await image.read()
    if len(image_bytes) == 0:
        raise HTTPException(status_code=400, detail="uploaded image is empty")
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"image exceeds {_MAX_IMAGE_BYTES // (1024*1024)} MB limit",
        )

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
        return templates.TemplateResponse(
            request=request,
            name="_error_panel.html",
            context={
                "heading": "Invalid application data",
                "message": str(exc),
            },
            status_code=200,
        )

    mime = _mime_from_upload(image)
    settings = get_settings()

    started = time.perf_counter()
    try:
        label_data = await extractor.extract(
            image_bytes=image_bytes,
            beverage_type=app_data.beverage_type,
            mime_type=mime,
        )
    except ExtractorError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return templates.TemplateResponse(
            request=request,
            name="_error_panel.html",
            context={
                "heading": "Vision model could not read this label",
                "message": (
                    f"{exc} (after {elapsed_ms} ms). Please try again in a moment, "
                    "or upload a sharper image."
                ),
            },
            status_code=200,
        )
    latency_ms = int((time.perf_counter() - started) * 1000)

    field_verdicts = verify_label(label_data, app_data)
    overall = Verdict.worst_of(fv.verdict for fv in field_verdicts.values())

    result = VerificationResult(
        overall=overall,
        field_verdicts=field_verdicts,
        raw_extraction=label_data,
        cache_hit=False,
        fallback_used=False,
        extractor_used=settings.extractor_provider,
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
