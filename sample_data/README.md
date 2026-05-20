# Sample labels (demo / fixture data)

The three samples here are **synthetic fixtures** that bypass the vision
extractor — `/sample/{name}` reads the JSON, runs only the deterministic
verifier, and renders the result. Lets the demo run offline without
burning Gemini quota.

| Sample            | Verdict | Why                                                       |
| ----------------- | ------- | --------------------------------------------------------- |
| `spirits-pass`    | PASS    | Clean Kentucky Straight Bourbon Whiskey label, all match. |
| `abv-fail`        | FAIL    | Label declares 48.5% ABV vs application's 45.0% (delta 3.5 pp ≫ 0.3 pp tolerance). Cites 27 CFR 5.65(b). |
| `warning-fail`    | FAIL    | Government-warning text is canonical but `caps_correct=false` — "Government Warning" rendered in title case. Cites 27 CFR 16.22. |

Each `<name>.json` file has two keys:

- `label`        — a full `LabelData` JSON in the §5.5 shape.
- `application`  — an `ApplicationData` JSON.

The image is a small placeholder PNG (`<name>.png`). Real label images
would replace these as the eval suite grows; the verifier logic doesn't
change.
