"""Tests for app.extractors.prompt — the extraction prompt builder.

The prompt is the wire contract between any vision model and the rest of
the system. It must:

  1. Specify every key from the §5.5 contract.
  2. Instruct the model to return null + "low" confidence rather than guess
     (MVP9 confidence gate depends on this signal being honest).
  3. Ask three yes/no formatting questions on the government warning so the
     §5.1 / 27 CFR 16.22 check has real input.
  4. Surface beverage-type conditionality so the model knows when an
     optional field is genuinely absent (not just unreadable).

These invariants are static-checkable — no network needed.
"""
from __future__ import annotations

import json
import re

import pytest

from app.models import BeverageType


# Every key the JSON schema in presearch §5.5 names.
REQUIRED_TOP_LEVEL_KEYS = {
    "brand_name",
    "class_type",
    "alcohol_content_pct",
    "alcohol_content_text",
    "net_contents",
    "bottler_name",
    "bottler_address",
    "country_of_origin",
    "government_warning_text",
    "government_warning_formatting",
}

WARNING_FORMATTING_SUBKEYS = {
    "caps_correct",
    "bold_correct",
    "continuous",
    "confidence",
}


class TestExtractionPrompt:
    def test_builder_returns_non_empty_string(self):
        from app.extractors.prompt import build_extraction_prompt

        prompt = build_extraction_prompt(BeverageType.DISTILLED_SPIRITS)
        assert isinstance(prompt, str)
        assert len(prompt) > 200  # not just a stub

    def test_prompt_mentions_every_required_field_key(self):
        """The model must see every key by name. If a key is missing from
        the prompt, the Pydantic `LabelData.model_validate_json` will fail
        on the response — which is correct but obscure to debug."""
        from app.extractors.prompt import build_extraction_prompt

        prompt = build_extraction_prompt(BeverageType.DISTILLED_SPIRITS)
        for key in REQUIRED_TOP_LEVEL_KEYS:
            assert key in prompt, f"prompt missing required key: {key!r}"

    def test_prompt_mentions_all_three_confidence_levels(self):
        from app.extractors.prompt import build_extraction_prompt

        prompt = build_extraction_prompt(BeverageType.WINE)
        assert "high" in prompt
        assert "medium" in prompt
        assert "low" in prompt

    def test_prompt_instructs_null_plus_low_over_guessing(self):
        """MVP9: any required field at 'low' is an ERROR. That contract
        only works if the prompt forbids hallucination. The instruction
        must appear *in words* the model can act on, not just in spirit."""
        from app.extractors.prompt import build_extraction_prompt

        prompt = build_extraction_prompt(BeverageType.DISTILLED_SPIRITS).lower()
        # We don't pin exact wording — but 'null' must appear paired with
        # 'low' near a 'do not guess' / 'rather than guess' instruction.
        assert "null" in prompt
        assert re.search(r"(do not guess|rather than guess|never guess|don't guess)", prompt)

    def test_warning_formatting_block_documents_all_three_questions(self):
        """Presearch §5.1: ask three yes/no questions on the warning —
        (a) caps on 'GOVERNMENT WARNING', (b) bold on that phrase,
        (c) continuous statement. The prompt must surface all three so
        the model populates `caps_correct`, `bold_correct`, `continuous`."""
        from app.extractors.prompt import build_extraction_prompt

        prompt = build_extraction_prompt(BeverageType.DISTILLED_SPIRITS)
        for key in WARNING_FORMATTING_SUBKEYS:
            assert key in prompt

        prompt_lower = prompt.lower()
        # The three concepts must be present (in any phrasing).
        assert "capital" in prompt_lower or "caps" in prompt_lower or "all caps" in prompt_lower
        assert "bold" in prompt_lower
        assert "continuous" in prompt_lower or "uninterrupted" in prompt_lower

    def test_prompt_surfaces_beverage_type_so_model_knows_context(self):
        """A wine label shouldn't be asked for a 'class/type' the same way
        as a malt beverage. Pass the beverage type into the prompt body."""
        from app.extractors.prompt import build_extraction_prompt

        spirits_prompt = build_extraction_prompt(
            BeverageType.DISTILLED_SPIRITS
        )
        wine_prompt = build_extraction_prompt(BeverageType.WINE)

        assert "distilled_spirits" in spirits_prompt or "spirits" in spirits_prompt.lower()
        assert "wine" in wine_prompt.lower()
        assert spirits_prompt != wine_prompt  # actually parameterised

    def test_prompt_includes_a_well_formed_json_example(self):
        """The model performs best when given a literal JSON example in
        the contract shape — and we can verify that example is itself
        parseable JSON, otherwise the model will mimic our typo."""
        from app.extractors.prompt import build_extraction_prompt

        prompt = build_extraction_prompt(BeverageType.DISTILLED_SPIRITS)
        # Pull the largest {...} substring and try to parse it.
        first_brace = prompt.find("{")
        last_brace = prompt.rfind("}")
        assert first_brace != -1 and last_brace > first_brace, "no JSON block in prompt"
        candidate = prompt[first_brace : last_brace + 1]

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            pytest.fail(f"prompt JSON example doesn't parse: {exc}")

        # Confirm the example carries every required key (not a partial schema).
        assert REQUIRED_TOP_LEVEL_KEYS.issubset(parsed.keys())
        wf = parsed["government_warning_formatting"]
        assert WARNING_FORMATTING_SUBKEYS.issubset(wf.keys())

    def test_prompt_forbids_markdown_fences(self):
        """The Gemini ERROR_FIX_LOG notes the SDK can wrap JSON in a string
        even with response_mime_type=application/json. Belt-and-braces:
        the prompt itself tells the model 'no markdown, no code fences'."""
        from app.extractors.prompt import build_extraction_prompt

        prompt = build_extraction_prompt(BeverageType.OTHER).lower()
        assert "no markdown" in prompt or "without markdown" in prompt

    def test_prompt_asks_model_to_guess_beverage_type(self):
        """For the upload-prefill flow the model must also return its best
        guess at the beverage_type so the form's dropdown can be pre-set.
        The agent is the source of truth for the regulatory classification
        — this is just a suggestion."""
        from app.extractors.prompt import build_extraction_prompt

        prompt = build_extraction_prompt(BeverageType.DISTILLED_SPIRITS)
        assert "beverage_type_guess" in prompt
        # the four allowed values must be mentioned so the model knows the closed set
        for v in ("distilled_spirits", "wine", "malt_beverage", "other"):
            assert v in prompt, f"prompt missing beverage_type_guess value: {v!r}"

    def test_json_example_includes_beverage_type_guess(self):
        """The literal example response should also carry the new key so
        the model mimics the right shape."""
        import json

        from app.extractors.prompt import build_extraction_prompt

        prompt = build_extraction_prompt(BeverageType.WINE)
        first_brace = prompt.find("{")
        last_brace = prompt.rfind("}")
        example = json.loads(prompt[first_brace : last_brace + 1])
        assert "beverage_type_guess" in example
        assert example["beverage_type_guess"] in {
            "distilled_spirits", "wine", "malt_beverage", "other"
        }
