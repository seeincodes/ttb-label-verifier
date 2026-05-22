"""Tests for GET /eval — STR5 in-app eval-suite dashboard.

Reads the most recent eval/results/eval-*.json (output of `make eval`)
and renders it as a full HTML page so reviewers visiting the deployed
URL see the eval-suite discipline directly, not just in docs. No HTMX
swap — full-page render like /sample/{name}.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


_EVAL_RESULTS = Path("eval/results")


@pytest.fixture
def isolated_results(tmp_path, monkeypatch):
    """Point the route at a temp eval/results dir for the test, so the test
    is hermetic regardless of whether the harness has been run locally.
    Returns the dir Path; tests write files into it to drive the route."""
    from app import main as main_module

    tmp_results = tmp_path / "results"
    tmp_results.mkdir()
    monkeypatch.setattr(main_module, "EVAL_RESULTS_DIR", tmp_results)
    yield tmp_results


def _write_result(
    results_dir: Path,
    *,
    name: str = "eval-20260522T120000Z.json",
    n_fixtures: int = 21,
    fp_rate: float = 0.0,
    fn_rate: float = 0.0,
    records: list[dict] | None = None,
) -> Path:
    payload = {
        "extractor_provider": "gemini",
        "mode": "fixture (verifier-only, no extractor calls)",
        "summary": {
            "n_fixtures": n_fixtures,
            "verdict_distribution": {"pass": 9, "warn": 2, "fail": 6, "error": 4},
            "expected_verdict_distribution": {"pass": 9, "warn": 2, "fail": 6, "error": 4},
            "false_positive_rate": fp_rate,
            "false_negative_rate": fn_rate,
            "latency_ms": {"p50": 0, "p95": 0, "p99": 0},
            "cost_per_label_usd": 0.0,
            "pricing_usd_per_label": {"gemini-2.5-flash": 0.000167, "gpt-4o": 0.00425},
            "cache_hit_rate": None,
            "per_field_accuracy": {
                "brand_name": 0.9048,
                "class_type": 0.9048,
                "alcohol_content": 0.8571,
                "net_contents": 0.9524,
                "bottler_name": 1.0,
                "bottler_address": 1.0,
                "country_of_origin": 0.5,
                "government_warning": 0.8571,
            },
            "bucket_breakdown": {
                "easy": {"pass": 5, "warn": 0, "fail": 0, "error": 0},
                "hard": {"pass": 0, "warn": 1, "fail": 0, "error": 4},
                "violations": {"pass": 0, "warn": 0, "fail": 5, "error": 0},
                "edge_cases": {"pass": 4, "warn": 1, "fail": 1, "error": 0},
            },
        },
        "records": records or [
            {
                "name": "easy_clean_bourbon",
                "bucket": "easy",
                "expected_overall": "pass",
                "actual_overall": "pass",
                "latency_ms": 0,
                "field_verdicts": {},
            },
            {
                "name": "violations_abv_abbreviation",
                "bucket": "violations",
                "expected_overall": "fail",
                "actual_overall": "fail",
                "latency_ms": 0,
                "field_verdicts": {},
            },
        ],
    }
    path = results_dir / name
    path.write_text(json.dumps(payload, indent=2))
    return path


class TestEvalDashboardEmptyState:
    """When eval/results/ has no files, render the empty-state guidance
    rather than 404 — the reviewer should learn how to populate it."""

    def test_empty_directory_renders_200_with_run_instruction(self, isolated_results):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/eval")
        assert resp.status_code == 200
        body = resp.text.lower()
        # Empty-state message must include something actionable
        assert "make eval" in body or "no eval" in body or "run" in body


class TestEvalDashboardHappyPath:
    def test_renders_headline_metrics(self, isolated_results):
        from app.main import app

        _write_result(isolated_results)
        client = TestClient(app)
        body = client.get("/eval").text

        # Headline numbers from the canned summary
        assert "21" in body  # n_fixtures
        # FP / FN rate should surface (zero is the noteworthy value)
        assert "0.0" in body or "0%" in body or "0.00" in body
        # The four-bucket distribution should be visible
        for verdict in ("pass", "warn", "fail", "error"):
            assert verdict in body.lower()

    def test_renders_per_fixture_table(self, isolated_results):
        from app.main import app

        _write_result(isolated_results)
        client = TestClient(app)
        body = client.get("/eval").text.lower()
        assert "<table" in body
        # Both record names land in the table
        assert "easy_clean_bourbon" in body
        assert "violations_abv_abbreviation" in body

    def test_full_page_extends_base(self, isolated_results):
        """Full-page render (not an HTMX fragment) — must include the
        base.html shell so it stands alone."""
        from app.main import app

        _write_result(isolated_results)
        client = TestClient(app)
        body = client.get("/eval").text.lower()
        assert "<!doctype" in body or "<html" in body
        # Tailwind CDN is the canonical signal that base.html rendered
        assert "tailwind" in body or "cdn.tailwindcss" in body


class TestEvalDashboardLatestFile:
    """When multiple result files exist, the latest (by timestamp filename)
    wins. The harness writes eval-YYYYMMDDTHHMMSSZ.json; lexicographic sort
    is correct because the timestamp is fixed-width."""

    def test_latest_timestamped_file_is_loaded(self, isolated_results):
        from app.main import app

        # Older file → should be ignored
        _write_result(
            isolated_results,
            name="eval-20260101T000000Z.json",
            n_fixtures=10,
        )
        # Newer file → should win
        _write_result(
            isolated_results,
            name="eval-20260522T120000Z.json",
            n_fixtures=21,
        )
        body = TestClient(app).get("/eval").text
        # The headline should be 21 (the newer file), never 10
        assert "21" in body


class TestEvalDashboardDisagreementHighlighting:
    def test_disagreement_row_visually_distinct(self, isolated_results):
        """A fixture where actual != expected should be highlighted so a
        reviewer scanning the table can spot it without reading every row."""
        from app.main import app

        _write_result(
            isolated_results,
            records=[
                {
                    "name": "deliberately_drifting_fixture",
                    "bucket": "edge_cases",
                    "expected_overall": "pass",
                    "actual_overall": "fail",  # disagreement
                    "latency_ms": 0,
                    "field_verdicts": {},
                },
                {
                    "name": "agreeing_fixture",
                    "bucket": "easy",
                    "expected_overall": "pass",
                    "actual_overall": "pass",
                    "latency_ms": 0,
                    "field_verdicts": {},
                },
            ],
        )
        body = TestClient(app).get("/eval").text
        # Some visual differentiation must exist for the disagreement row —
        # either an amber/red styling class near the row, or a "✗" / "drift"
        # marker.
        assert (
            "drift" in body.lower()
            or "disagree" in body.lower()
            or "✗" in body
            or "amber" in body
            or "bg-red" in body.lower()
        )


class TestEvalDashboardNavLink:
    def test_base_html_links_to_eval(self):
        """A reviewer landing on / should be able to navigate to /eval
        via the nav — not just by typing the URL."""
        from app.main import app

        body = TestClient(app).get("/").text.lower()
        assert "/eval" in body
