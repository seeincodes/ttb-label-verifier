# Eval harness

Verifier-only eval against `eval/test_set/` (20 hand-authored JSON
fixtures across 4 buckets — see `eval/test_set/GENERATION.md`).

## Run

```bash
make eval
```

Prints a summary to stdout and writes a per-fixture record set to
`eval/results/<timestamp>.json` (gitignored).

## Metrics

- **verdict distribution** — pass / warn / fail / error counts overall
  and per bucket.
- **false-positive rate** — expected FAIL but the verifier returned PASS.
  Denominator is "labels that should have failed"; non-FAIL-expected
  fixtures don't dilute. This is the **single most important metric**
  for a compliance product — a silent regulatory false-PASS is the
  worst failure mode.
- **false-negative rate** — expected PASS but the verifier returned
  FAIL. Less bad than FP but still erodes trust.
- **per-field PASS rate** — by checklist field (brand, class, ABV, net
  contents, bottler, country, warning).
- **latency p50 / p95 / p99** — verifier hot path only in fixture mode
  (sub-millisecond, doesn't say much). In real-image mode this includes
  the model call.
- **cost per label** — zero in fixture mode; pricing constants for the
  real-image mode live at the top of `harness.py`.
- **cache hit rate** — N/A in fixture mode; placeholder in the JSON
  output so the shape is stable for the real run.

## Gemini vs. OpenAI A / B

Fixture mode bypasses the extractor, so the A/B comparison is **not**
exercised here — the verifier path is provider-agnostic by design (the
prompt is the same for both providers, `LabelData` is the same shape
after validation).

When the harness runs against real label images (future work — keep
the same fixtures and swap the extractor call), the comparison looks
like:

```bash
EXTRACTOR_PROVIDER=gemini make eval
mv eval/results/<latest>.json eval/results/gemini-baseline.json

EXTRACTOR_PROVIDER=openai make eval
mv eval/results/<latest>.json eval/results/openai-baseline.json
```

The two result JSONs are diffable; the README §9 table is the
human-readable version of that diff. Expected dimensions to surface:

| Dimension                  | Gemini 2.5 Flash | GPT-4o |
| -------------------------- | ---------------- | ------ |
| p50 latency (cache miss)   | ~1.5 s           | ~3 s   |
| Cost per label             | ~$0.000167       | ~$0.005 |
| Hard-bucket ERROR rate     | lower            | lower (better OCR on degraded images per presearch) |
| Easy-bucket PASS rate      | both should be 5/5 (the easy bucket exists to confirm neither model spuriously fails) |
| FP rate on violations      | both should be 0 (the verifier is the FP-prevention layer, not the model) |

The verifier's FP rate should be **identical** across providers because
the verifier doesn't look at the image — it consumes `LabelData`. Any
divergence between providers on the violations bucket is a prompt-
adherence or extraction-quality issue, not a verifier issue.

## Why fixture mode is the prototype default

`docs/MEMO.md` §10 + `CLAUDE.md` lock no-persistence for the prototype.
Real label images would require a corpus we don't ship (PII risk +
storage). The fixtures exercise every verifier code path including the
MVP9 confidence gate edge cases that real-image mode would only
occasionally hit. When the eval corpus is later promoted out of
prototype scope, the harness is ready: drop in images, point at the
real extractor, the metrics math doesn't change.
