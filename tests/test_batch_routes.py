"""Route tests for the batch flow.

Covers:
  GET  /batch              — page render
  POST /batch              — accepts files (+ optional CSV), returns {run_id}
  GET  /batch/stream/{id}  — SSE stream of row + progress + done events
  GET  /batch/export/{id}.csv — CSV download once the run has completed

The extractor and cache are injected via FastAPI deps and overridden per
test so the suite never touches a real model.
"""
from __future__ import annotations

import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app.extractors.base import LabelExtractor
from app.models import (
    BeverageType,
    ExtractedField,
    LabelData,
    WarningFormatting,
)
from tests._helpers import good_synthetic_png


def _canonical_warning_text():
    from app.verifier.warning import canonical_warning_text

    return canonical_warning_text()


def _fake_label() -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value="OLD TOM DISTILLERY", confidence="high"),
        class_type=ExtractedField(
            value="Kentucky Straight Bourbon Whiskey", confidence="high"
        ),
        alcohol_content_pct=ExtractedField(value=45.0, confidence="high"),
        alcohol_content_text=ExtractedField(value="45% ALC./VOL.", confidence="high"),
        net_contents=ExtractedField(value="750 mL", confidence="high"),
        bottler_name=ExtractedField(value="Old Tom Distillery LLC", confidence="high"),
        bottler_address=ExtractedField(value="123 Distillery Rd", confidence="high"),
        country_of_origin=ExtractedField(value=None, confidence="high"),
        government_warning_text=ExtractedField(
            value=_canonical_warning_text(), confidence="high"
        ),
        government_warning_formatting=WarningFormatting(
            caps_correct=True, bold_correct=True, continuous=True, confidence="high"
        ),
    )


class StubExtractor(LabelExtractor):
    def __init__(self, canned: LabelData) -> None:
        self.canned = canned

    async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
        return self.canned


SYNTHETIC_PNG = good_synthetic_png()


MIN_CSV = (
    "filename,beverage_type,brand_name,class_type,alcohol_content_pct,"
    "net_contents,bottler_name,bottler_address,is_import,country_of_origin\n"
    "a.png,distilled_spirits,Old Tom Distillery,Kentucky Straight Bourbon Whiskey,"
    "45.0,750 mL,Old Tom Distillery LLC,123 Distillery Rd,false,\n"
    "b.png,distilled_spirits,Old Tom Distillery,Kentucky Straight Bourbon Whiskey,"
    "45.0,750 mL,Old Tom Distillery LLC,123 Distillery Rd,false,\n"
)


@pytest.fixture
def client_and_store():
    """Fresh BatchStore + LabelDataCache + stub extractor per test."""
    from app.batch import BatchStore, get_batch_store
    from app.cache import LabelDataCache, get_cache
    from app.dependencies import get_extractor
    from app.main import app

    store = BatchStore()
    cache = LabelDataCache(maxsize=64)
    stub = StubExtractor(_fake_label())

    app.dependency_overrides[get_extractor] = lambda: stub
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_batch_store] = lambda: store

    yield TestClient(app), store, stub
    app.dependency_overrides.clear()


class TestGetBatchPage:
    def test_returns_html_with_dropzone(self, client_and_store):
        client, _, _ = client_and_store
        resp = client.get("/batch")
        assert resp.status_code == 200
        body = resp.text.lower()
        # Dropzone affordance: x-data with file handling, or a labelled input
        assert 'type="file"' in body
        assert "multiple" in body  # batch accepts multiple files
        # CSV upload is offered
        assert "csv" in body
        # Filter chips affordance — labels for the four filter states
        for chip in ("all", "failures", "warnings", "ok"):
            assert chip in body, f"filter chip {chip!r} missing"


class TestPostBatch:
    def test_accepts_two_files_and_returns_run_id(self, client_and_store):
        client, store, _ = client_and_store
        files = [
            ("files", ("a.png", BytesIO(SYNTHETIC_PNG), "image/png")),
            ("files", ("b.png", BytesIO(SYNTHETIC_PNG + b"\x00"), "image/png")),
        ]
        resp = client.post(
            "/batch",
            files=files,
            data={"expected_csv": MIN_CSV},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert "run_id" in payload
        # run is registered in the store
        run = store.get(payload["run_id"])
        assert run is not None
        assert len(run.items) == 2
        assert "a.png" in run.expected and "b.png" in run.expected

    def test_no_files_rejected(self, client_and_store):
        client, _, _ = client_and_store
        # no files key at all
        resp = client.post("/batch", data={"expected_csv": ""})
        assert resp.status_code in (400, 422)

    def test_run_without_csv_uses_no_expected_data(self, client_and_store):
        """CSV is optional — without it, each row should later error per
        label with 'no expected application data provided', not crash."""
        client, store, _ = client_and_store
        files = [("files", ("a.png", BytesIO(SYNTHETIC_PNG), "image/png"))]
        resp = client.post("/batch", files=files, data={"expected_csv": ""})
        assert resp.status_code == 200
        run = store.get(resp.json()["run_id"])
        assert run is not None
        assert run.expected == {}


class TestBatchStream:
    def test_unknown_run_returns_404(self, client_and_store):
        client, _, _ = client_and_store
        with client.stream("GET", "/batch/stream/unknown") as resp:
            assert resp.status_code == 404

    def test_streams_row_progress_and_done_events(self, client_and_store):
        client, store, _ = client_and_store
        # Create a run via the API.
        files = [
            ("files", ("a.png", BytesIO(SYNTHETIC_PNG), "image/png")),
            ("files", ("b.png", BytesIO(SYNTHETIC_PNG + b"\x00"), "image/png")),
        ]
        resp = client.post("/batch", files=files, data={"expected_csv": MIN_CSV})
        run_id = resp.json()["run_id"]

        events: list[tuple[str, str]] = []
        with client.stream("GET", f"/batch/stream/{run_id}") as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            current_event = None
            current_data: list[str] = []
            for raw_line in r.iter_lines():
                line = raw_line if isinstance(raw_line, str) else raw_line.decode()
                if line.startswith("event:"):
                    current_event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    current_data.append(line[len("data:") :].strip())
                elif line == "" and current_event:
                    events.append((current_event, "\n".join(current_data)))
                    current_event = None
                    current_data = []

        kinds = [e[0] for e in events]
        # Two rows + at least one progress (more is fine) + one done.
        assert kinds.count("row") == 2
        assert "progress" in kinds
        assert kinds.count("done") == 1

    def test_row_event_contains_html_fragment(self, client_and_store):
        client, _, _ = client_and_store
        files = [("files", ("a.png", BytesIO(SYNTHETIC_PNG), "image/png"))]
        resp = client.post("/batch", files=files, data={"expected_csv": MIN_CSV})
        run_id = resp.json()["run_id"]

        with client.stream("GET", f"/batch/stream/{run_id}") as r:
            buf = b""
            for chunk in r.iter_raw():
                buf += chunk
                if b"event: done" in buf:
                    break
        body = buf.decode("utf-8", errors="ignore")
        # row event data should embed a <tr or <article tag
        assert "<tr" in body.lower() or "<article" in body.lower()
        # PASS verdict from the canonical label should be visible
        assert "pass" in body.lower()


class TestBatchExportCsv:
    def test_unknown_run_returns_404(self, client_and_store):
        client, _, _ = client_and_store
        resp = client.get("/batch/export/unknown.csv")
        assert resp.status_code == 404

    def test_export_returns_csv_after_stream_completes(self, client_and_store):
        client, _, _ = client_and_store
        files = [
            ("files", ("a.png", BytesIO(SYNTHETIC_PNG), "image/png")),
            ("files", ("b.png", BytesIO(SYNTHETIC_PNG + b"\x00"), "image/png")),
        ]
        resp = client.post("/batch", files=files, data={"expected_csv": MIN_CSV})
        run_id = resp.json()["run_id"]

        # Drain the stream first so results are populated.
        with client.stream("GET", f"/batch/stream/{run_id}") as r:
            for _ in r.iter_raw():
                pass

        export = client.get(f"/batch/export/{run_id}.csv")
        assert export.status_code == 200
        assert "text/csv" in export.headers["content-type"]
        text = export.text
        assert "filename" in text.lower()
        assert "a.png" in text and "b.png" in text
