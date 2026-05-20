"""Tests for app.batch — in-memory batch runs + bounded-concurrent runner."""
from __future__ import annotations

import asyncio
from io import BytesIO

import pytest

from app.extractors.base import LabelExtractor
from app.models import (
    ApplicationData,
    BeverageType,
    ExtractedField,
    LabelData,
    WarningFormatting,
)


def _canonical_warning_text():
    from app.verifier.warning import canonical_warning_text

    return canonical_warning_text()


def _fake_label(brand="Acme") -> LabelData:
    return LabelData(
        brand_name=ExtractedField(value=brand, confidence="high"),
        class_type=ExtractedField(value="Bourbon", confidence="high"),
        alcohol_content_pct=ExtractedField(value=45.0, confidence="high"),
        alcohol_content_text=ExtractedField(value="45% ALC./VOL.", confidence="high"),
        net_contents=ExtractedField(value="750 mL", confidence="high"),
        bottler_name=ExtractedField(value=brand, confidence="high"),
        bottler_address=ExtractedField(value="1 Main", confidence="high"),
        country_of_origin=ExtractedField(value=None, confidence="high"),
        government_warning_text=ExtractedField(
            value=_canonical_warning_text(), confidence="high"
        ),
        government_warning_formatting=WarningFormatting(
            caps_correct=True, bold_correct=True, continuous=True, confidence="high"
        ),
    )


def _fake_app(brand="Acme") -> ApplicationData:
    return ApplicationData(
        beverage_type=BeverageType.DISTILLED_SPIRITS,
        brand_name=brand,
        class_type="Bourbon",
        alcohol_content_pct=45.0,
        net_contents="750 mL",
        bottler_name=brand,
        bottler_address="1 Main",
        is_import=False,
    )


class SlowStubExtractor(LabelExtractor):
    """Records call timestamps so we can assert bounded concurrency works."""

    def __init__(self, canned: LabelData, delay: float = 0.05) -> None:
        self.canned = canned
        self.delay = delay
        self.concurrent_calls = 0
        self.peak_concurrent = 0

    async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
        self.concurrent_calls += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent_calls)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.concurrent_calls -= 1
        return self.canned


class TestBatchStore:
    def test_create_run_returns_uuid_run_id(self):
        from app.batch import BatchStore

        store = BatchStore()
        run = store.create_run(
            items=[("label1.png", b"\x00", "image/png")],
            expected={"label1.png": _fake_app()},
        )
        assert isinstance(run.run_id, str)
        assert len(run.run_id) >= 16  # uuid-ish

    def test_get_run_returns_same_instance(self):
        from app.batch import BatchStore

        store = BatchStore()
        run = store.create_run(items=[], expected={})
        assert store.get(run.run_id) is run

    def test_get_unknown_run_returns_none(self):
        from app.batch import BatchStore

        store = BatchStore()
        assert store.get("not-a-run") is None


class TestBatchRunner:
    """Bounded concurrency invariant: with concurrency=N and 10 items,
    the peak concurrent extractor calls never exceeds N."""

    def test_concurrency_bound_enforced(self):
        from app.batch import BatchStore, run_batch
        from app.cache import LabelDataCache

        items = [(f"label{i}.png", b"\x00" + str(i).encode(), "image/png") for i in range(10)]
        expected = {f: _fake_app() for f, _, _ in items}

        store = BatchStore()
        run = store.create_run(items=items, expected=expected)
        stub = SlowStubExtractor(_fake_label(), delay=0.02)
        cache = LabelDataCache(maxsize=64)

        concurrency = 3

        async def _drain():
            async for _event in run_batch(run, stub, cache, concurrency=concurrency):
                pass

        asyncio.run(_drain())
        assert stub.peak_concurrent <= concurrency, (
            f"peak {stub.peak_concurrent} > limit {concurrency}"
        )

    def test_emits_row_event_per_label(self):
        from app.batch import BatchStore, run_batch
        from app.cache import LabelDataCache

        items = [(f"label{i}.png", b"\x00" + str(i).encode(), "image/png") for i in range(5)]
        expected = {f: _fake_app() for f, _, _ in items}
        store = BatchStore()
        run = store.create_run(items=items, expected=expected)
        stub = SlowStubExtractor(_fake_label(), delay=0.005)
        cache = LabelDataCache(maxsize=64)

        async def _collect():
            events = []
            async for event in run_batch(run, stub, cache, concurrency=3):
                events.append(event)
            return events

        events = asyncio.run(_collect())
        row_events = [e for e in events if e["event"] == "row"]
        done_events = [e for e in events if e["event"] == "done"]
        assert len(row_events) == 5
        assert len(done_events) == 1

    def test_run_state_populated_after_completion(self):
        from app.batch import BatchStore, run_batch
        from app.cache import LabelDataCache

        items = [(f"label{i}.png", b"\x00" + str(i).encode(), "image/png") for i in range(3)]
        expected = {f: _fake_app() for f, _, _ in items}
        store = BatchStore()
        run = store.create_run(items=items, expected=expected)
        stub = SlowStubExtractor(_fake_label())
        cache = LabelDataCache(maxsize=64)

        async def _drain():
            async for _ in run_batch(run, stub, cache, concurrency=2):
                pass

        asyncio.run(_drain())
        assert run.status == "complete"
        assert len(run.results) == 3
        for filename in (i[0] for i in items):
            assert filename in run.results

    def test_extractor_error_recorded_per_label(self):
        """One label failing must not crash the whole batch — its result
        is recorded as an error, the others complete normally."""
        from app.batch import BatchStore, run_batch
        from app.cache import LabelDataCache
        from app.extractors.gemini import ExtractorError

        class HalfFailingExtractor(LabelExtractor):
            def __init__(self):
                self.calls = 0

            async def extract(self, image_bytes, beverage_type, mime_type="image/jpeg"):
                self.calls += 1
                if self.calls == 2:
                    raise ExtractorError("simulated 503")
                return _fake_label()

        items = [(f"label{i}.png", b"\x00" + str(i).encode(), "image/png") for i in range(3)]
        expected = {f: _fake_app() for f, _, _ in items}
        store = BatchStore()
        run = store.create_run(items=items, expected=expected)
        stub = HalfFailingExtractor()
        cache = LabelDataCache(maxsize=64)

        async def _drain():
            async for _ in run_batch(run, stub, cache, concurrency=2):
                pass

        asyncio.run(_drain())
        assert run.status == "complete"
        # Three results, one is an error.
        error_count = sum(1 for r in run.results.values() if r.get("error"))
        assert error_count == 1


class TestParseExpectedCsv:
    """The batch route accepts an optional CSV mapping filename → expected
    application data. Header row is the ApplicationData field names."""

    def test_parses_minimal_csv(self):
        from app.batch import parse_expected_csv

        csv_text = (
            "filename,beverage_type,brand_name,class_type,alcohol_content_pct,"
            "net_contents,bottler_name,bottler_address,is_import,country_of_origin\n"
            "label1.png,distilled_spirits,Acme,Bourbon,45.0,750 mL,Acme,1 Main,false,\n"
        )
        expected = parse_expected_csv(csv_text)
        assert "label1.png" in expected
        app = expected["label1.png"]
        assert app.beverage_type is BeverageType.DISTILLED_SPIRITS
        assert app.brand_name == "Acme"
        assert app.alcohol_content_pct == pytest.approx(45.0)
        assert app.is_import is False

    def test_handles_import_row(self):
        from app.batch import parse_expected_csv

        csv_text = (
            "filename,beverage_type,brand_name,class_type,alcohol_content_pct,"
            "net_contents,bottler_name,bottler_address,is_import,country_of_origin\n"
            "scotch.png,distilled_spirits,Glen X,Scotch Whisky,40.0,750 mL,Glen X,1 Highland,true,Scotland\n"
        )
        expected = parse_expected_csv(csv_text)
        app = expected["scotch.png"]
        assert app.is_import is True
        assert app.country_of_origin == "Scotland"

    def test_returns_empty_dict_for_empty_csv(self):
        from app.batch import parse_expected_csv

        assert parse_expected_csv("") == {}
        assert parse_expected_csv("filename,beverage_type\n") == {}


class TestResultsToCsv:
    """CSV export — one row per processed label, with verdict + reason."""

    def test_includes_header_and_one_row_per_result(self):
        from app.batch import BatchRun, results_to_csv

        run = BatchRun(
            run_id="r1",
            items=[("a.png", b"", "image/png"), ("b.png", b"", "image/png")],
            expected={},
        )
        run.results = {
            "a.png": {"overall": "pass", "field_summary": "ok", "error": None},
            "b.png": {"overall": "fail", "field_summary": "abv mismatch", "error": None},
        }
        run.status = "complete"

        text = results_to_csv(run)
        lines = text.strip().splitlines()
        assert len(lines) == 3  # header + 2 rows
        assert "filename" in lines[0].lower()
        assert "overall" in lines[0].lower()
        # Order should be the same as items; both rows present
        assert "a.png" in lines[1] and "pass" in lines[1].lower()
        assert "b.png" in lines[2] and "fail" in lines[2].lower()
