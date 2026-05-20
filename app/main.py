from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings

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


@app.get("/health", response_class=JSONResponse)
async def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "extractor": settings.extractor_provider}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"extractor": settings.extractor_provider},
    )
