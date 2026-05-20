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

    Built via the factory in `app.extractors.build_extractor()` which
    selects on `EXTRACTOR_PROVIDER` and wraps the primary in a
    `FallbackExtractor` so a transient primary failure retries once with
    the secondary. Cached so the SDK client is built once per process.
    """
    from app.extractors import build_extractor

    return build_extractor()
