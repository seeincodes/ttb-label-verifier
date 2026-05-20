"""FastAPI dependency providers.

Routes accept dependencies via `Depends(get_extractor)` etc. Production
wiring builds the real extractor; tests override via
`app.dependency_overrides[get_extractor] = ...` so the unit suite never
hits the real Gemini SDK.
"""
from __future__ import annotations

from functools import lru_cache

from app.extractors.base import LabelExtractor


@lru_cache(maxsize=1)
def get_extractor() -> LabelExtractor:
    """Return the production `LabelExtractor`.

    Wired to Gemini per the locked default `EXTRACTOR_PROVIDER=gemini`.
    Task group 10 will add an OpenAI branch + fallback. Cached so the
    SDK client is built once per process.
    """
    # Import here to keep route imports cheap when tests override the dep.
    from app.extractors.gemini import GeminiExtractor

    return GeminiExtractor.from_settings()
