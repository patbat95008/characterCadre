"""Unit tests for validation.py."""
from __future__ import annotations

import asyncio
import pytest

from app.fixtures import BRAM, CHARACTERS, NARRATOR, SCENARIO, make_stage1_save
from app.models import Beat, DirectorResponse, Scenario, Save
from app.validation import (
    Err,
    Ok,
    OPTIONS_FALLBACK,
    is_loop,
    validate_director_response,
    validate_options_response,
    validate_streamed_text,
    with_validation,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_beat(order: int, bid: str = "") -> Beat:
    bid = bid or f"beat-{order}"
    return Beat(
        id=bid,
        order=order,
        name=f"Beat {order}",
        description="desc",
        summary="",
        transition_condition="condition",
        starter_prompt="starter",
    )


def _fixture_save(**kwargs):
    save = make_stage1_save()
    for k, v in kwargs.items():
        setattr(save, k, v)
    return save


def _valid_director_raw(speaker_id: str = "bram") -> dict:
    return {
        "speaker_character_id": speaker_id,
        "dm_should_narrate": True,
        "beat_transition": False,
        "next_beat_id": None,
        "direction_note": "Describe the dungeon entrance.",
        "reasoning": "User just arrived.",
    }


# ── validate_director_response ────────────────────────────────────────────────

class TestValidateDirectorResponse:

    def test_valid_response_ok(self):
        raw = _valid_director_raw("bram")
        result = validate_director_response(raw, SCENARIO, CHARACTERS, _fixture_save())
        assert isinstance(result, Ok)
        assert result.value.speaker_character_id == "bram"

    def test_null_speaker_is_valid(self):
        # Null speaker means "no follow-up speaker; player goes next."
        raw = {**_valid_director_raw("bram"), "speaker_character_id": None}
        result = validate_director_response(raw, SCENARIO, CHARACTERS, _fixture_save())
        assert isinstance(result, Ok)
        assert result.value.speaker_character_id is None

    def test_unknown_speaker_returns_err(self):
        raw = _valid_director_raw("unknown-id")
        result = validate_director_response(raw, SCENARIO, CHARACTERS, _fixture_save())
        assert isinstance(result, Err)
        assert "active roster" in result.reason

    def test_speaker_not_in_characters_dict_returns_err(self):
        raw = _valid_director_raw("narrator")
        save = _fixture_save(active_character_ids=["narrator", "bram"])
        # Remove narrator from characters dict
        chars = {BRAM.id: BRAM}
        result = validate_director_response(raw, SCENARIO, chars, save)
        assert isinstance(result, Err)
        assert "not found in characters" in result.reason

    def test_missing_required_field_returns_err(self):
        raw = {"speaker_character_id": "bram", "beat_transition": False}
        result = validate_director_response(raw, SCENARIO, CHARACTERS, _fixture_save())
        assert isinstance(result, Err)
        assert "schema validation" in result.reason

    def test_beat_transition_in_sandbox_mode_returns_err(self):
        b0 = _make_beat(0, "b0")
        sc = SCENARIO.model_copy(update={"beats": [b0]})
        raw = {**_valid_director_raw("bram"), "beat_transition": True, "next_beat_id": "b0"}
        save = _fixture_save(current_beat_id=None, sandbox_mode=True)
        result = validate_director_response(raw, sc, CHARACTERS, save)
        assert isinstance(result, Err)
        assert "sandbox" in result.reason

    def test_beat_transition_no_beats_returns_err(self):
        raw = {**_valid_director_raw("bram"), "beat_transition": True, "next_beat_id": "b0"}
        save = _fixture_save()
        sc_no_beats = SCENARIO.model_copy(update={"beats": []})
        result = validate_director_response(raw, sc_no_beats, CHARACTERS, save)
        assert isinstance(result, Err)
        assert "no beats" in result.reason

    def test_beat_transition_without_next_beat_id_returns_err(self):
        b0 = _make_beat(0, "b0")
        sc = SCENARIO.model_copy(update={"beats": [b0]})
        raw = {**_valid_director_raw("bram"), "beat_transition": True, "next_beat_id": None}
        save = _fixture_save(current_beat_id="b0", sandbox_mode=False)
        result = validate_director_response(raw, sc, CHARACTERS, save)
        assert isinstance(result, Err)
        assert "next_beat_id" in result.reason

    def test_beat_transition_backward_jump_returns_err(self):
        b0, b1 = _make_beat(0, "b0"), _make_beat(1, "b1")
        sc = SCENARIO.model_copy(update={"beats": [b0, b1]})
        # Current is b1 (order=1), trying to go back to b0 (order=0)
        raw = {**_valid_director_raw("bram"), "beat_transition": True, "next_beat_id": "b0"}
        save = _fixture_save(current_beat_id="b1", sandbox_mode=False)
        result = validate_director_response(raw, sc, CHARACTERS, save)
        assert isinstance(result, Err)
        assert "not forward" in result.reason

    def test_beat_transition_forward_jump_ok(self):
        b0, b1, b2 = _make_beat(0, "b0"), _make_beat(1, "b1"), _make_beat(2, "b2")
        sc = SCENARIO.model_copy(update={"beats": [b0, b1, b2]})
        # Current is b0 (order=0), skipping to b2 (order=2)
        raw = {**_valid_director_raw("bram"), "beat_transition": True, "next_beat_id": "b2"}
        save = _fixture_save(current_beat_id="b0", sandbox_mode=False)
        result = validate_director_response(raw, sc, CHARACTERS, save)
        assert isinstance(result, Ok)
        assert result.value.next_beat_id == "b2"

    def test_beat_transition_to_nonexistent_beat_returns_err(self):
        b0 = _make_beat(0, "b0")
        sc = SCENARIO.model_copy(update={"beats": [b0]})
        raw = {**_valid_director_raw("bram"), "beat_transition": True, "next_beat_id": "no-such-id"}
        save = _fixture_save(current_beat_id="b0", sandbox_mode=False)
        result = validate_director_response(raw, sc, CHARACTERS, save)
        assert isinstance(result, Err)
        assert "not found" in result.reason


# ── validate_options_response ─────────────────────────────────────────────────

def _opt(text: str, advances_beat: bool = False, dice_roll: dict | None = None) -> dict:
    return {"text": text, "advances_beat": advances_beat, "dice_roll": dice_roll}


class TestValidateOptionsResponse:

    def test_valid_four_options_ok(self):
        raw = {"options": [_opt("A"), _opt("B"), _opt("C"), _opt("D", True)]}
        result = validate_options_response(raw)
        assert isinstance(result, Ok)
        assert [o["text"] for o in result.value] == ["A", "B", "C", "D"]
        assert result.value[3]["advances_beat"] is True

    def test_counts_in_range_ok(self):
        for n in (2, 3, 4, 5, 6):
            raw = {"options": [_opt(f"opt{i}") for i in range(n)]}
            assert isinstance(validate_options_response(raw), Ok), f"len={n} should be Ok"

    def test_wrong_count_returns_err(self):
        assert isinstance(validate_options_response({"options": []}), Err)
        assert isinstance(validate_options_response({"options": [_opt("A")]}), Err)
        assert isinstance(
            validate_options_response({"options": [_opt(f"o{i}") for i in range(7)]}), Err
        )

    def test_empty_string_in_options_returns_err(self):
        result = validate_options_response({"options": [_opt("A"), _opt(""), _opt("C"), _opt("D")]})
        assert isinstance(result, Err)
        assert "empty" in result.reason

    def test_missing_options_key_returns_err(self):
        result = validate_options_response({"choices": [_opt("A"), _opt("B"), _opt("C"), _opt("D")]})
        assert isinstance(result, Err)

    def test_non_dict_returns_err(self):
        result = validate_options_response(["A", "B", "C", "D"])  # type: ignore[arg-type]
        assert isinstance(result, Err)

    def test_non_object_item_returns_err(self):
        result = validate_options_response({"options": ["A", "B", "C", "D"]})
        assert isinstance(result, Err)
        assert "not an object" in result.reason

    def test_missing_text_field_returns_err(self):
        result = validate_options_response({"options": [{"advances_beat": False}, _opt("B"), _opt("C"), _opt("D")]})
        assert isinstance(result, Err)
        assert "text" in result.reason

    def test_non_bool_advances_beat_returns_err(self):
        result = validate_options_response({"options": [{"text": "A", "advances_beat": "yes"}, _opt("B"), _opt("C"), _opt("D")]})
        assert isinstance(result, Err)
        assert "advances_beat" in result.reason

    def test_multiple_advances_beat_clamped_to_one(self):
        raw = {"options": [_opt("A", True), _opt("B", True), _opt("C"), _opt("D")]}
        result = validate_options_response(raw)
        assert isinstance(result, Ok)
        advance_count = sum(1 for o in result.value if o["advances_beat"])
        assert advance_count == 1

    def test_whitespace_only_text_returns_err(self):
        result = validate_options_response({"options": [_opt("A"), _opt("   "), _opt("C"), _opt("D")]})
        assert isinstance(result, Err)


# ── validate_streamed_text ─────────────────────────────────────────────────────

class TestValidateStreamedText:

    def test_non_empty_text_ok(self):
        result = validate_streamed_text("Hello there!", None, False)
        assert isinstance(result, Ok)

    def test_empty_string_returns_err(self):
        result = validate_streamed_text("", None, False)
        assert isinstance(result, Err)

    def test_whitespace_only_returns_err(self):
        result = validate_streamed_text("   \n\t  ", None, False)
        assert isinstance(result, Err)

    def test_no_previous_message_skips_loop_check(self):
        result = validate_streamed_text("Hello!", None, True)
        assert isinstance(result, Ok)

    def test_exact_repeat_same_speaker_is_err(self):
        text = "You see a dark and foreboding corridor stretching ahead into the shadow."
        result = validate_streamed_text(text, text, same_speaker=True)
        assert isinstance(result, Err)
        assert "loop" in result.reason

    def test_exact_repeat_different_speaker_is_err(self):
        text = "You see a dark and foreboding corridor stretching ahead into the shadow."
        result = validate_streamed_text(text, text, same_speaker=False)
        assert isinstance(result, Err)

    def test_high_similarity_same_speaker_is_err(self):
        text1 = "You see a dark and foreboding corridor stretching ahead into shadow forever."
        text2 = "You see a dark and foreboding corridor stretching ahead into darkness forever."
        result = validate_streamed_text(text1, text2, same_speaker=True)
        assert isinstance(result, Err)

    def test_dissimilar_text_is_ok(self):
        text1 = "You see a dark corridor."
        text2 = "Bram shrugs and hefts his axe. We stay together, lad."
        result = validate_streamed_text(text1, text2, same_speaker=False)
        assert isinstance(result, Ok)

    def test_short_identical_replies_are_not_flagged(self):
        # Short replies (< LOOP_MIN_LENGTH normalized) bypass loop detection.
        text = "Yes, of course."
        result = validate_streamed_text(text, text, same_speaker=True)
        assert isinstance(result, Ok)


# ── is_loop ───────────────────────────────────────────────────────────────────

class TestIsLoop:

    def test_identical_long_strings_always_loop(self):
        text = "The same long sentence repeated verbatim across two consecutive turns."
        loop, ratio = is_loop(text, text, same_speaker=True)
        assert loop is True
        assert ratio == pytest.approx(1.0)

    def test_identical_long_different_speaker_still_loop(self):
        text = "The same long sentence repeated verbatim across two consecutive turns."
        loop, ratio = is_loop(text, text, same_speaker=False)
        assert loop is True

    def test_different_strings_not_loop(self):
        loop, _ = is_loop(
            "A completely different thing said by one of the party members aloud.",
            "Something else entirely happens in this scene with no overlap at all.",
            same_speaker=True,
        )
        assert loop is False

    def test_short_identical_strings_are_not_loop(self):
        # Below LOOP_MIN_LENGTH the check is skipped to avoid false positives.
        loop, ratio = is_loop("Yes.", "Yes.", same_speaker=True)
        assert loop is False
        assert ratio == 0.0

    def test_empty_strings_are_not_loop(self):
        loop, _ = is_loop("", "", same_speaker=True)
        assert loop is False

    def test_normalization_ignores_punctuation(self):
        # Use long-enough strings so the length floor doesn't short-circuit.
        text1 = "Hello there friend, how are you doing on this fine evening!"
        text2 = "hello there friend how are you doing on this fine evening"
        loop, ratio = is_loop(text1, text2, same_speaker=True)
        assert ratio > 0.95
        assert loop is True


# ── with_validation ───────────────────────────────────────────────────────────

class TestWithValidation:

    def test_succeeds_on_first_try(self):
        async def run():
            call_count = 0
            async def call_fn():
                nonlocal call_count
                call_count += 1
                return {"options": [_opt("A"), _opt("B"), _opt("C"), _opt("D")]}
            result = await with_validation(call_fn, validate_options_response)
            assert [o["text"] for o in result] == ["A", "B", "C", "D"]
            assert call_count == 1
        asyncio.get_event_loop().run_until_complete(run())

    def test_retries_on_failure_then_succeeds(self):
        async def run():
            attempts = []
            async def call_fn():
                attempts.append(1)
                if len(attempts) == 1:
                    return {"options": [_opt("A")]}  # invalid (count too low)
                return {"options": [_opt("A"), _opt("B"), _opt("C"), _opt("D")]}
            result = await with_validation(call_fn, validate_options_response)
            assert [o["text"] for o in result] == ["A", "B", "C", "D"]
            assert len(attempts) == 2
        asyncio.get_event_loop().run_until_complete(run())

    def test_calls_on_retry_callback(self):
        async def run():
            retry_reasons = []
            async def call_fn():
                return {"options": ["too few"]}
            async def on_retry(reason):
                retry_reasons.append(reason)
            await with_validation(
                call_fn,
                validate_options_response,
                max_retries=2,
                on_failure=lambda: list(OPTIONS_FALLBACK),
                on_retry=on_retry,
            )
            assert len(retry_reasons) == 2  # 2 retries before fallback
        asyncio.get_event_loop().run_until_complete(run())

    def test_falls_back_after_exhaustion(self):
        async def run():
            async def call_fn():
                return {"bad": "data"}
            result = await with_validation(
                call_fn,
                validate_options_response,
                max_retries=2,
                on_failure=lambda: ["fallback"],
            )
            assert result == ["fallback"]
        asyncio.get_event_loop().run_until_complete(run())

    def test_raises_if_no_fallback_after_exhaustion(self):
        async def run():
            async def call_fn():
                return {"bad": "data"}
            with pytest.raises(RuntimeError, match="Validation failed"):
                await with_validation(call_fn, validate_options_response, max_retries=0)
        asyncio.get_event_loop().run_until_complete(run())
