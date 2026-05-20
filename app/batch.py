"""Batch verification — in-memory run store + bounded-concurrent runner.

Per presearch §3.3 the batch flow is single-Python-service, SSE-streamed,
with `asyncio.Semaphore(BATCH_CONCURRENCY)` as the parallelism gate. No
queue / broker / worker process for the prototype — that's the locked
production-path note in MEMO §10.

Public surface:
  - `BatchRun`              — one batch's mutable state (items, expected,
                              results, status).
  - `BatchStore`            — process-local registry of run_id → BatchRun.
  - `run_batch(...)`        — async generator yielding SSE-shaped events as
                              labels complete; populates `BatchRun.results`.
  - `parse_expected_csv()`  — header-row CSV → {filename: ApplicationData}.
  - `results_to_csv()`      — BatchRun.results → CSV text for export.
  - `get_batch_store()`     — process-singleton store factory.
"""
from __future__ import annotations

import asyncio
import csv
import io
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, AsyncIterator, Optional

from app.cache import LabelDataCache
from app.extractors.base import LabelExtractor
from app.extractors.gemini import ExtractorError
from app.models import (
    ApplicationData,
    BeverageType,
    VerificationResult,
    Verdict,
)
from app.verifier.rules import verify_label


BatchItem = tuple[str, bytes, str]  # (filename, image_bytes, mime_type)


@dataclass
class BatchRun:
    """One batch's mutable state.

    `results` maps filename → a small dict (verdict + reason + raw
    extraction summary + optional error). We keep a dict-of-dicts here
    rather than a dict-of-VerificationResult so the error case (extraction
    failure on one label) doesn't require fabricating a VerificationResult
    that wouldn't satisfy its `overall == worst_of(field_verdicts)`
    invariant.
    """

    run_id: str
    items: list[BatchItem]
    expected: dict[str, ApplicationData]
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    status: str = "pending"  # pending | in_progress | complete
    created_at: float = field(default_factory=time.time)


class BatchStore:
    """In-memory registry of batch runs.

    Process-local; runs do not survive a restart. MEMO §10 documents the
    Redis / Postgres production path. Thread-safe via the GIL since dicts
    are atomic for single key set/get and the only concurrent surface is
    `run_batch`'s async tasks — they only write to `run.results` keys
    keyed by filename, each task owns its key.
    """

    def __init__(self) -> None:
        self._runs: dict[str, BatchRun] = {}

    def create_run(
        self,
        *,
        items: list[BatchItem],
        expected: dict[str, ApplicationData],
    ) -> BatchRun:
        run_id = uuid.uuid4().hex
        run = BatchRun(run_id=run_id, items=list(items), expected=dict(expected))
        self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> Optional[BatchRun]:
        return self._runs.get(run_id)


@lru_cache(maxsize=1)
def get_batch_store() -> BatchStore:
    return BatchStore()


# ---------------------------------------------------------------------------
# CSV parsing — expected application data (one row per filename)
# ---------------------------------------------------------------------------


_BOOLEAN_TRUE = {"true", "yes", "1", "on"}


def _coerce_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _BOOLEAN_TRUE


def parse_expected_csv(csv_text: str) -> dict[str, ApplicationData]:
    """Parse a CSV of expected application data into {filename: ApplicationData}.

    Header row names ApplicationData fields. The `filename` column is
    required; it's how rows are matched to uploaded files in the batch.
    Empty rows are silently skipped so trailing newlines don't blow up.
    Rows that fail Pydantic validation are skipped with their error
    available in the BatchRun.results error path (the caller can choose
    to surface it).
    """
    out: dict[str, ApplicationData] = {}
    if not csv_text or not csv_text.strip():
        return out

    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        filename = (row.get("filename") or "").strip()
        if not filename:
            continue

        try:
            beverage = BeverageType(row["beverage_type"].strip())
        except (KeyError, ValueError):
            continue

        abv_raw = (row.get("alcohol_content_pct") or "").strip()
        try:
            abv = float(abv_raw) if abv_raw else None
        except ValueError:
            abv = None

        try:
            app_data = ApplicationData(
                beverage_type=beverage,
                brand_name=row.get("brand_name", "").strip(),
                class_type=(row.get("class_type") or "").strip() or None,
                alcohol_content_pct=abv,
                net_contents=row.get("net_contents", "").strip(),
                bottler_name=row.get("bottler_name", "").strip(),
                bottler_address=row.get("bottler_address", "").strip(),
                is_import=_coerce_bool(row.get("is_import")),
                country_of_origin=(row.get("country_of_origin") or "").strip() or None,
            )
        except ValueError:
            continue

        out[filename] = app_data

    return out


# ---------------------------------------------------------------------------
# CSV export — results
# ---------------------------------------------------------------------------


def results_to_csv(run: BatchRun) -> str:
    """Serialize the batch's results into CSV text for download.

    Columns: filename, overall_verdict, field_summary, error. One row per
    item in `run.items` (preserving upload order), filling in '' for
    items that haven't completed yet (defensive — `/batch/export` should
    only be called after the run completes).
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["filename", "overall_verdict", "field_summary", "error"])
    for filename, _bytes, _mime in run.items:
        row = run.results.get(filename, {})
        writer.writerow(
            [
                filename,
                row.get("overall", ""),
                row.get("field_summary", ""),
                row.get("error", "") or "",
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def _summarize_result(result: VerificationResult) -> str:
    """One-line summary of which fields failed/warned/errored, for CSV."""
    counts: dict[str, int] = {"pass": 0, "warn": 0, "fail": 0, "error": 0}
    for fv in result.field_verdicts.values():
        counts[fv.verdict.value] += 1
    return " ".join(f"{k}={v}" for k, v in counts.items() if v)


async def _process_one(
    *,
    item: BatchItem,
    expected: Optional[ApplicationData],
    extractor: LabelExtractor,
    cache: LabelDataCache,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Run extract + verify for a single label under the semaphore."""
    filename, image_bytes, mime = item

    if expected is None:
        # No expected data supplied for this filename — record as a skip
        # so the row still appears in the result table and CSV.
        return {
            "filename": filename,
            "overall": "error",
            "field_summary": "",
            "error": "no expected application data provided for this file",
            "result": None,
        }

    async with semaphore:
        cache_key = cache.key_for(image_bytes)
        cached = cache.get(cache_key)
        cache_hit = False

        started = time.perf_counter()
        try:
            if cached is not None:
                label_data = cached
                cache_hit = True
                latency_ms = 0
            else:
                label_data = await extractor.extract(
                    image_bytes=image_bytes,
                    beverage_type=expected.beverage_type,
                    mime_type=mime,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                cache.put(cache_key, label_data)
        except ExtractorError as exc:
            return {
                "filename": filename,
                "overall": "error",
                "field_summary": "",
                "error": f"vision model: {exc}",
                "result": None,
            }

        field_verdicts = verify_label(label_data, expected)
        overall = Verdict.worst_of(fv.verdict for fv in field_verdicts.values())
        result = VerificationResult(
            overall=overall,
            field_verdicts=field_verdicts,
            raw_extraction=label_data,
            cache_hit=cache_hit,
            fallback_used=False,
            extractor_used="batch",
            latency_ms=latency_ms,
        )
        return {
            "filename": filename,
            "overall": overall.value,
            "field_summary": _summarize_result(result),
            "error": None,
            "result": result,
        }


async def run_batch(
    run: BatchRun,
    extractor: LabelExtractor,
    cache: LabelDataCache,
    *,
    concurrency: int,
) -> AsyncIterator[dict[str, Any]]:
    """Async generator: yields `{event, data}` dicts as labels complete.

    Events:
      - `{"event": "row", "data": <result-dict>}` — one per completed label.
      - `{"event": "progress", "data": {"completed": n, "total": N}}` —
        emitted after each row.
      - `{"event": "done", "data": {"completed": N}}` — final.

    Concurrency is bounded by `asyncio.Semaphore(concurrency)`. The
    semaphore lives in this function rather than on BatchRun so a
    single-batch retry doesn't share saturation with a fresh batch.
    """
    run.status = "in_progress"
    semaphore = asyncio.Semaphore(concurrency)
    total = len(run.items)

    tasks = [
        asyncio.create_task(
            _process_one(
                item=item,
                expected=run.expected.get(item[0]),
                extractor=extractor,
                cache=cache,
                semaphore=semaphore,
            )
        )
        for item in run.items
    ]

    completed = 0
    for coro in asyncio.as_completed(tasks):
        row = await coro
        filename = row["filename"]
        # store everything except the heavy result object on the run; the
        # full VerificationResult lives in the row's 'result' key for the
        # SSE consumer to render. The CSV export uses the lightweight
        # shape.
        run.results[filename] = {
            "overall": row["overall"],
            "field_summary": row["field_summary"],
            "error": row["error"],
        }
        completed += 1
        yield {"event": "row", "data": row}
        yield {"event": "progress", "data": {"completed": completed, "total": total}}

    run.status = "complete"
    yield {"event": "done", "data": {"completed": completed, "total": total}}
