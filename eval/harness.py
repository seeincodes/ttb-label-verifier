"""Eval harness — fixture-driven, verifier-only mode (MVP13).

Reads every fixture in `eval/test_set/labels/` and the matching expected
record in `eval/test_set/expected/`, runs each through the existing
`verify_label` orchestrator (no extractor call — fixtures supply
pre-canned `LabelData` JSON), and reports the metrics from presearch
§6.2:

  - per-field accuracy (extracted vs expected, when the fixture's
    `application` is parsed as `ApplicationData`)
  - false-positive rate    (expected FAIL, actual PASS — worst case)
  - false-negative rate    (expected PASS, actual FAIL)
  - verdict distribution   (pass / warn / fail / error counts)
  - p50 / p95 / p99 latency over the verifier hot path
  - cost per label          (zero in fixture mode; pricing constants
                             defined here for the future real-image mode)
  - cache hit rate          (N/A in fixture mode; placeholder reported
                             so the JSON shape is stable for the real run)

The harness deliberately bypasses the extractor so the eval runs
offline, deterministically, in CI. The same fixtures can later be
re-played through the real extractor by swapping the `LabelData`
construction for an `await extractor.extract(image_bytes, ...)` call —
the metrics math is unchanged.

Run via `make eval`. Summary goes to stdout; the full per-fixture
record set goes to `eval/results/<timestamp>.json` (gitignored).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from app.models import (
    ApplicationData,
    FieldVerdict,
    LabelData,
    Verdict,
)
from app.verifier.rules import verify_label


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

EVAL_DIR = Path(__file__).resolve().parent
TEST_SET_DIR = EVAL_DIR / "test_set"
LABELS_DIR = TEST_SET_DIR / "labels"
EXPECTED_DIR = TEST_SET_DIR / "expected"
RESULTS_DIR = EVAL_DIR / "results"


# ---------------------------------------------------------------------------
# Pricing constants — only consulted in real-image mode (not fixture mode).
# Sourced from public vendor pricing pages; numbers in presearch §3.2 / §6.2
# are the basis for the cost-per-label MVP13 metric.
# Kept here so the harness has a single canonical source if a future run
# wants $/label numbers without re-googling.
# ---------------------------------------------------------------------------

_PRICING_USD_PER_LABEL = {
    # ~$1.67 / 10k pages for Gemini 2.5 Flash (presearch §3.2). Encoded as
    # the per-label cost so the metric is direct.
    "gemini": 0.000167,
    # OpenAI GPT-4o vision pricing varies by image size; the figure here is
    # a rough per-label estimate. Update when the real-image eval runs.
    "openai": 0.00500,
}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalRecord:
    """One fixture's run through the verifier."""

    name: str
    bucket: str
    expected_overall: Verdict
    actual_overall: Verdict
    field_verdicts: dict[str, FieldVerdict]
    latency_ms: int
    # Optional: per-field-accuracy comparisons get attached here so the
    # accuracy metric in `summarise` doesn't need to re-walk the verifier.
    field_accuracy: dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Metrics helpers (pure — tested in tests/test_eval_harness.py)
# ---------------------------------------------------------------------------


def false_positive_rate(pairs: Sequence[tuple[Verdict, Verdict]]) -> float:
    """FP = expected FAIL but harness verdict was PASS.

    Denominator is "labels that should have failed" — never the full
    test-set size, because including PASS-expected fixtures would dilute
    the metric and hide regressions.

    Pairs whose expected verdict is not FAIL are excluded from both the
    numerator AND the denominator. ERROR-expected fixtures (e.g. blurry
    image scenarios) are excluded too — ERROR isn't a regulatory verdict.
    """
    relevant = [actual for expected, actual in pairs if expected is Verdict.FAIL]
    if not relevant:
        return 0.0
    misses = sum(1 for v in relevant if v is Verdict.PASS)
    return misses / len(relevant)


def false_negative_rate(pairs: Sequence[tuple[Verdict, Verdict]]) -> float:
    """FN = expected PASS but harness verdict was FAIL.

    Denominator is "labels that should have passed". A WARN on a PASS-
    expected label is *not* a false negative — WARN is the right verdict
    when the agent should glance at the label, even if the underlying
    label is fine.
    """
    relevant = [actual for expected, actual in pairs if expected is Verdict.PASS]
    if not relevant:
        return 0.0
    misses = sum(1 for v in relevant if v is Verdict.FAIL)
    return misses / len(relevant)


def latency_percentiles(latencies_ms: Iterable[int]) -> dict[str, int]:
    """Return {p50, p95, p99} over the latency samples.

    For small samples (~20 fixtures) we use nearest-rank rather than
    linear interpolation past the observed max — extrapolating p99 of 20
    samples would invent latency the harness never measured.
    """
    data = sorted(int(x) for x in latencies_ms)
    if not data:
        return {"p50": 0, "p95": 0, "p99": 0}

    n = len(data)

    def _rank(p: float) -> int:
        # Nearest-rank, 1-indexed: ceil(p/100 * N), clamped to [1, N].
        import math

        rank = math.ceil((p / 100.0) * n)
        return max(1, min(n, rank))

    return {
        "p50": data[_rank(50) - 1],
        "p95": data[_rank(95) - 1],
        "p99": data[_rank(99) - 1],
    }


def verdict_distribution(verdicts: Iterable[Verdict]) -> dict[str, int]:
    """Counts per verdict, always with all four keys present.

    Downstream consumers (README §9, results JSON) don't have to
    defensively check for missing verdict types.
    """
    out = {"pass": 0, "warn": 0, "fail": 0, "error": 0}
    for v in verdicts:
        out[v.value] += 1
    return out


# ---------------------------------------------------------------------------
# Per-field accuracy
# ---------------------------------------------------------------------------


# The seven TTB-checklist fields plus the warning. Per-field accuracy is
# "did the verifier pass on this field given the fixture's stated truth?"
# — i.e. for fixtures the only way a field can be inaccurate is if the
# verifier itself disagrees with the fixture authors. That's the right
# question in fixture mode: it pins our verifier against a known-good
# hand-checked dataset. In real-image mode the same metric uses extracted-
# vs-application comparison directly.
_ACCURACY_FIELDS = (
    "brand_name",
    "class_type",
    "alcohol_content",
    "net_contents",
    "bottler_name",
    "bottler_address",
    "country_of_origin",
    "government_warning",
)


def per_field_accuracy(records: Sequence[EvalRecord]) -> dict[str, float]:
    """For each checklist field, fraction of fixtures where the actual
    verdict matched the fixture's expected per-field verdict.

    For fixtures that don't include the per-field 'expected' truth (the
    fixture only records `expected_overall`), we treat the verifier's
    output as authoritative — those fields contribute to neither
    numerator nor denominator. The fixture-author workflow: hand-build a
    fixture, run the harness once, copy the per-field verdicts into
    `expected` if you want them locked. For now we surface field-level
    PASS-rate as a proxy for the accuracy metric and call it that in the
    output.
    """
    totals: dict[str, int] = {f: 0 for f in _ACCURACY_FIELDS}
    passes: dict[str, int] = {f: 0 for f in _ACCURACY_FIELDS}
    for rec in records:
        for f in _ACCURACY_FIELDS:
            fv = rec.field_verdicts.get(f)
            if fv is None:
                continue
            totals[f] += 1
            if fv.verdict is Verdict.PASS:
                passes[f] += 1
    return {
        f: (passes[f] / totals[f] if totals[f] else 0.0)
        for f in _ACCURACY_FIELDS
    }


# ---------------------------------------------------------------------------
# Bucket breakdown
# ---------------------------------------------------------------------------


_KNOWN_BUCKETS = ("easy", "hard", "violations", "edge_cases")


def bucket_breakdown(records: Sequence[EvalRecord]) -> dict[str, dict[str, int]]:
    """Per-bucket verdict distribution so the README can show whether each
    of the four §6.1 buckets behaves as designed (easy → PASS, violations
    → FAIL, etc.)."""
    out: dict[str, dict[str, int]] = {
        b: {"pass": 0, "warn": 0, "fail": 0, "error": 0} for b in _KNOWN_BUCKETS
    }
    for rec in records:
        b = rec.bucket if rec.bucket in out else "easy"
        out[b][rec.actual_overall.value] += 1
    return out


# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------


def _load_fixture(name: str, labels_dir: Path, expected_dir: Path) -> EvalRecord:
    """Load one fixture from disk and run it through `verify_label`.

    Fixture file shape (matches `sample_data/spirits-pass.json` plus a
    `bucket` + `expected_overall`):

      labels/<name>.json:
        {<LabelData JSON, §5.5 shape>}

      expected/<name>.json:
        {
          "application":       {<ApplicationData JSON>},
          "expected_overall":  "pass" | "warn" | "fail" | "error",
          "bucket":            "easy" | "hard" | "violations" | "edge_cases"
        }
    """
    label_path = labels_dir / f"{name}.json"
    expected_path = expected_dir / f"{name}.json"
    if not label_path.exists():
        raise FileNotFoundError(f"label fixture missing: {label_path}")
    if not expected_path.exists():
        raise FileNotFoundError(f"expected fixture missing: {expected_path}")

    label_data = LabelData.model_validate(json.loads(label_path.read_text()))
    expected_payload = json.loads(expected_path.read_text())
    app_data = ApplicationData.model_validate(expected_payload["application"])
    expected_overall = Verdict(expected_payload["expected_overall"])
    bucket = expected_payload.get("bucket", "easy")

    started = time.perf_counter()
    field_verdicts = verify_label(label_data, app_data)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    actual_overall = Verdict.worst_of(fv.verdict for fv in field_verdicts.values())

    return EvalRecord(
        name=name,
        bucket=bucket,
        expected_overall=expected_overall,
        actual_overall=actual_overall,
        field_verdicts=field_verdicts,
        latency_ms=elapsed_ms,
    )


def discover_fixture_names(labels_dir: Path) -> list[str]:
    """Stable sorted list of `<name>` for every `<name>.json` in labels_dir."""
    if not labels_dir.exists():
        return []
    return sorted(p.stem for p in labels_dir.glob("*.json"))


def run_fixtures(
    *,
    labels_dir: Path = LABELS_DIR,
    expected_dir: Path = EXPECTED_DIR,
) -> list[EvalRecord]:
    """Run every fixture and return the EvalRecord list."""
    names = discover_fixture_names(labels_dir)
    return [_load_fixture(n, labels_dir, expected_dir) for n in names]


# ---------------------------------------------------------------------------
# Summarise + dump
# ---------------------------------------------------------------------------


def summarise(records: Sequence[EvalRecord]) -> dict:
    """Aggregate all records into the metric block written to disk + stdout.

    Cost-per-label is zero in fixture mode (no model calls). The pricing
    constants are surfaced under `pricing_usd_per_label` so a future real-
    image run can multiply them by the verdict count. Cache hit rate is
    None (not applicable) for the same reason; documented in eval/README.md.
    """
    pairs = [(r.expected_overall, r.actual_overall) for r in records]
    return {
        "n_fixtures": len(records),
        "verdict_distribution": verdict_distribution(
            r.actual_overall for r in records
        ),
        "expected_verdict_distribution": verdict_distribution(
            r.expected_overall for r in records
        ),
        "false_positive_rate": false_positive_rate(pairs),
        "false_negative_rate": false_negative_rate(pairs),
        "latency_ms": latency_percentiles(r.latency_ms for r in records),
        "cost_per_label_usd": 0.0,
        "pricing_usd_per_label": dict(_PRICING_USD_PER_LABEL),
        "cache_hit_rate": None,
        "per_field_accuracy": per_field_accuracy(records),
        "bucket_breakdown": bucket_breakdown(records),
    }


def _records_to_dump(records: Sequence[EvalRecord]) -> list[dict]:
    """Render each record as a JSON-safe dict (FieldVerdict has the Pydantic
    `model_dump`; Verdict is a str-enum so dumps cleanly too)."""
    out = []
    for r in records:
        out.append(
            {
                "name": r.name,
                "bucket": r.bucket,
                "expected_overall": r.expected_overall.value,
                "actual_overall": r.actual_overall.value,
                "latency_ms": r.latency_ms,
                "field_verdicts": {
                    k: fv.model_dump(mode="json")
                    for k, fv in r.field_verdicts.items()
                },
            }
        )
    return out


# ---------------------------------------------------------------------------
# Stdout summary table
# ---------------------------------------------------------------------------


def _format_summary_table(summary: dict, extractor: str) -> str:
    """Compact human-readable summary. Aligned columns; one line per metric
    so a `make eval` reader can eyeball the result without paging through
    the full JSON."""
    dist = summary["verdict_distribution"]
    expected_dist = summary["expected_verdict_distribution"]
    bb = summary["bucket_breakdown"]
    lat = summary["latency_ms"]

    lines = [
        "================================================================",
        f"  TTB Label Verification — eval harness (MVP13)",
        f"  extractor: {extractor}  (fixture-mode: verifier-only, no model calls)",
        "================================================================",
        f"  fixtures              : {summary['n_fixtures']}",
        f"  verdict (actual)      : pass={dist['pass']} warn={dist['warn']} "
        f"fail={dist['fail']} error={dist['error']}",
        f"  verdict (expected)    : pass={expected_dist['pass']} warn={expected_dist['warn']} "
        f"fail={expected_dist['fail']} error={expected_dist['error']}",
        f"  false_positive_rate   : {summary['false_positive_rate']:.4f}"
        f"   (expected FAIL silently PASSed — worst-case for compliance)",
        f"  false_negative_rate   : {summary['false_negative_rate']:.4f}"
        f"   (expected PASS spuriously FAILed)",
        f"  latency_ms            : p50={lat['p50']} p95={lat['p95']} p99={lat['p99']}",
        f"  cost_per_label_usd    : {summary['cost_per_label_usd']:.6f}"
        f"   (fixture-mode bypasses extractor — see eval/README.md)",
        f"  cache_hit_rate        : N/A in fixture mode",
        "  per-field PASS rate:",
    ]
    for f, rate in summary["per_field_accuracy"].items():
        lines.append(f"    {f:22s}: {rate:.2%}")
    lines.append("  bucket breakdown (actual verdict counts):")
    for b, dist_b in bb.items():
        lines.append(
            f"    {b:22s}: pass={dist_b['pass']} warn={dist_b['warn']} "
            f"fail={dist_b['fail']} error={dist_b['error']}"
        )
    lines.append("================================================================")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _timestamp_filename() -> str:
    return datetime.now(timezone.utc).strftime("eval-%Y%m%dT%H%M%SZ.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels-dir",
        type=Path,
        default=LABELS_DIR,
        help="directory of LabelData fixtures (default: eval/test_set/labels/)",
    )
    parser.add_argument(
        "--expected-dir",
        type=Path,
        default=EXPECTED_DIR,
        help="directory of expected ApplicationData + verdict (default: eval/test_set/expected/)",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR,
        help="where to write the per-run JSON (default: eval/results/)",
    )
    args = parser.parse_args(argv)

    extractor = os.environ.get("EXTRACTOR_PROVIDER", "gemini")

    records = run_fixtures(
        labels_dir=args.labels_dir,
        expected_dir=args.expected_dir,
    )
    if not records:
        print(
            f"no fixtures found in {args.labels_dir} — see eval/test_set/GENERATION.md",
            file=sys.stderr,
        )
        return 1

    summary = summarise(records)
    print(_format_summary_table(summary, extractor=extractor))

    args.results_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.results_dir / _timestamp_filename()
    out_path.write_text(
        json.dumps(
            {
                "extractor_provider": extractor,
                "mode": "fixture (verifier-only, no extractor calls)",
                "summary": summary,
                "records": _records_to_dump(records),
            },
            indent=2,
        )
    )
    print(f"\nwrote per-run JSON → {out_path.relative_to(EVAL_DIR.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
