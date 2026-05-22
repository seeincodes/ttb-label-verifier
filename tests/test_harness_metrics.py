"""Tests for the eval-harness metrics math.

The harness (`eval/harness.py`) aggregates across all 20 fixtures. Tests
here pin the math (false-positive rate, false-negative rate, latency
percentile, verdict distribution) in isolation so regressions are
obvious — when the real-image mode lands later, these tests are what
catch a math bug before it ships to README §9.

Also a single end-to-end test confirming the harness runs across the
checked-in fixture set without crashing and that actual==expected
across every fixture (drift detector for the fixture set itself).
"""
from __future__ import annotations

from app.models import Verdict


class TestFalsePositiveRate:
    """FP = expected FAIL but actual PASS. Denominator: expected-FAIL only."""

    def test_zero_when_no_fail_expected_fixtures(self):
        from eval.harness import false_positive_rate

        pairs = [(Verdict.PASS, Verdict.PASS)] * 3
        assert false_positive_rate(pairs) == 0.0

    def test_one_miss_in_three_fails(self):
        from eval.harness import false_positive_rate

        pairs = [
            (Verdict.FAIL, Verdict.FAIL),
            (Verdict.FAIL, Verdict.PASS),  # silent regulatory false-PASS
            (Verdict.FAIL, Verdict.FAIL),
        ]
        assert abs(false_positive_rate(pairs) - 1 / 3) < 1e-9

    def test_pass_expected_not_in_denominator(self):
        """Mixing PASS-expected fixtures into the input must NOT dilute FP."""
        from eval.harness import false_positive_rate

        pairs = [
            (Verdict.FAIL, Verdict.PASS),
            (Verdict.PASS, Verdict.PASS),
            (Verdict.PASS, Verdict.PASS),
        ]
        assert false_positive_rate(pairs) == 1.0

    def test_error_expected_excluded(self):
        """ERROR isn't a regulatory verdict — exclude from FP."""
        from eval.harness import false_positive_rate

        pairs = [
            (Verdict.ERROR, Verdict.PASS),
            (Verdict.FAIL, Verdict.FAIL),
        ]
        assert false_positive_rate(pairs) == 0.0

    def test_warn_actual_not_counted_as_fp(self):
        from eval.harness import false_positive_rate

        pairs = [(Verdict.FAIL, Verdict.WARN), (Verdict.FAIL, Verdict.FAIL)]
        assert false_positive_rate(pairs) == 0.0


class TestFalseNegativeRate:
    """FN = expected PASS but actual FAIL. WARN on PASS-expected is OK."""

    def test_zero_when_no_pass_expected_fixtures(self):
        from eval.harness import false_negative_rate

        pairs = [(Verdict.FAIL, Verdict.FAIL)] * 5
        assert false_negative_rate(pairs) == 0.0

    def test_one_miss_in_four_passes(self):
        from eval.harness import false_negative_rate

        pairs = [
            (Verdict.PASS, Verdict.PASS),
            (Verdict.PASS, Verdict.PASS),
            (Verdict.PASS, Verdict.FAIL),
            (Verdict.PASS, Verdict.PASS),
        ]
        assert false_negative_rate(pairs) == 0.25

    def test_warn_on_pass_expected_not_counted_as_fn(self):
        from eval.harness import false_negative_rate

        pairs = [(Verdict.PASS, Verdict.WARN), (Verdict.PASS, Verdict.PASS)]
        assert false_negative_rate(pairs) == 0.0


class TestLatencyPercentiles:
    def test_empty_returns_zeros(self):
        from eval.harness import latency_percentiles

        assert latency_percentiles([]) == {"p50": 0, "p95": 0, "p99": 0}

    def test_single_value_returns_that_value_for_all(self):
        from eval.harness import latency_percentiles

        assert latency_percentiles([42]) == {"p50": 42, "p95": 42, "p99": 42}

    def test_nearest_rank_not_interpolation(self):
        """20 samples sorted ascending; p95 = 19th value (ceil(0.95*20)=19),
        p99 = 20th. Nearest-rank avoids inventing values past the max."""
        from eval.harness import latency_percentiles

        samples = list(range(1, 21))
        result = latency_percentiles(samples)
        assert result["p50"] == 10
        assert result["p95"] == 19
        assert result["p99"] == 20

    def test_unsorted_input_handled(self):
        from eval.harness import latency_percentiles

        samples = [100, 1, 50, 200, 25, 75, 10, 5]
        result = latency_percentiles(samples)
        # sorted: [1, 5, 10, 25, 50, 75, 100, 200]
        # p50 → ceil(0.5*8) = 4 → 25
        assert result["p50"] == 25


class TestVerdictDistribution:
    def test_empty_returns_all_zeros(self):
        from eval.harness import verdict_distribution

        assert verdict_distribution([]) == {
            "pass": 0,
            "warn": 0,
            "fail": 0,
            "error": 0,
        }

    def test_all_four_keys_always_present(self):
        from eval.harness import verdict_distribution

        result = verdict_distribution([Verdict.PASS, Verdict.PASS, Verdict.PASS])
        assert set(result.keys()) == {"pass", "warn", "fail", "error"}
        assert result["error"] == 0

    def test_counts(self):
        from eval.harness import verdict_distribution

        result = verdict_distribution(
            [Verdict.PASS, Verdict.PASS, Verdict.WARN, Verdict.FAIL, Verdict.ERROR]
        )
        assert result == {"pass": 2, "warn": 1, "fail": 1, "error": 1}


class TestHarnessEndToEnd:
    """Run the real harness across the checked-in fixture set and confirm
    the fixture set itself is consistent (actual == expected per fixture).
    This is the drift detector — a fixture whose expected_overall stops
    agreeing with what verify_label actually returns is a fixture bug
    OR a verifier regression; either way, this test surfaces it loudly."""

    def test_runs_all_fixtures_and_actual_matches_expected(self):
        from eval.harness import run_fixtures

        records = run_fixtures()
        assert len(records) >= 20  # the checked-in set

        mismatches = [
            r for r in records if r.actual_overall is not r.expected_overall
        ]
        assert not mismatches, (
            f"fixture-vs-verifier drift in: "
            f"{[r.name + ': expected ' + r.expected_overall.value + ' got ' + r.actual_overall.value for r in mismatches]}"
        )

    def test_bucket_distribution_at_least_5_per_bucket(self):
        """Presearch §6.1 calls for 5 per bucket as the minimum coverage.
        Buckets may grow over time (e.g. STR6 wine-class-boundary added a
        6th edge case after the initial 20-fixture lay-down) — invariant
        is *at least* 5 each, no bucket missing or undersized."""
        from eval.harness import run_fixtures

        records = run_fixtures()
        buckets: dict[str, int] = {}
        for r in records:
            buckets[r.bucket] = buckets.get(r.bucket, 0) + 1
        assert buckets.get("easy", 0) >= 5
        assert buckets.get("hard", 0) >= 5
        assert buckets.get("violations", 0) >= 5
        assert buckets.get("edge_cases", 0) >= 5
