"""
Integration tests for phases.py.
Patches structured_chat and stream_chat to avoid real Ollama calls.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from app.fixtures import BRAM, CHARACTERS, NARRATOR, SCENARIO, make_stage1_save
from app.models import Beat, Message, Scenario
from app.phases import apply_beat_transition, run_director, run_phase2, run_phase3
from app.validation import OPTIONS_FALLBACK


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_beat(order: int, bid: str = "") -> Beat:
    bid = bid or f"beat-{order}"
    return Beat(
        id=bid,
        order=order,
        name=f"Beat {order}",
        description=f"Beat {order} description",
        summary=f"Beat {order} summary",
        transition_condition=f"Beat {order} condition",
        starter_prompt=f"Beat {order} begins.",
    )


def _fixture_save(**kwargs):
    save = make_stage1_save()
    for k, v in kwargs.items():
        setattr(save, k, v)
    return save


def _valid_director_dict(speaker_id: str = "bram", beat_transition: bool = False, next_beat_id: str = None) -> dict:
    return {
        "speaker_character_id": speaker_id,
        "dm_should_narrate": True,
        "beat_transition": beat_transition,
        "next_beat_id": next_beat_id,
        "direction_note": "Describe the dungeon.",
        "reasoning": "Making progress.",
    }


async def _collect_phase2(save, scenario, characters, director_response) -> list[dict]:
    gen = await run_phase2(save, scenario, characters, director_response)
    events = []
    async for event in gen:
        events.append(event)
    return events


# ── Phase 1: run_director ─────────────────────────────────────────────────────

class TestRunDirector:

    async def test_happy_path_returns_director_response(self):
        save = _fixture_save()
        raw = _valid_director_dict("bram")
        with patch("app.phases.structured_chat", new=AsyncMock(return_value=raw)):
            result = await run_director(save, SCENARIO, CHARACTERS)
        assert result.speaker_character_id == "bram"
        assert result.dm_should_narrate is True
        assert result.beat_transition is False

    async def test_retries_on_invalid_speaker(self):
        calls = []
        async def mock_structured(model, messages, schema, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return _valid_director_dict("unknown-id")
            return _valid_director_dict("bram")
        with patch("app.phases.structured_chat", side_effect=mock_structured):
            result = await run_director(_fixture_save(), SCENARIO, CHARACTERS)
        assert result.speaker_character_id == "bram"
        assert len(calls) == 2

    async def test_falls_back_after_exhaustion(self):
        async def mock_structured(model, messages, schema, **kwargs):
            return _valid_director_dict("nobody")  # always invalid
        with patch("app.phases.structured_chat", side_effect=mock_structured):
            result = await run_director(_fixture_save(), SCENARIO, CHARACTERS)
        # Fallback uses first non-DM character
        assert result.speaker_character_id == BRAM.id
        assert result.dm_should_narrate is True
        assert result.beat_transition is False

    async def test_calls_on_retry_callback(self):
        retry_reasons = []
        async def on_retry(reason: str):
            retry_reasons.append(reason)
        async def mock_structured(model, messages, schema, **kwargs):
            return _valid_director_dict("nobody")
        with patch("app.phases.structured_chat", side_effect=mock_structured):
            await run_director(_fixture_save(), SCENARIO, CHARACTERS, on_retry=on_retry)
        assert len(retry_reasons) > 0


# ── apply_beat_transition ─────────────────────────────────────────────────────

class TestApplyBeatTransition:

    def test_no_transition_when_flag_false(self):
        from app.models import DirectorResponse
        dr = DirectorResponse(
            speaker_character_id="bram",
            dm_should_narrate=False,
            beat_transition=False,
            next_beat_id=None,
            direction_note="",
        )
        result = apply_beat_transition(_fixture_save(), SCENARIO, dr)
        assert result is None

    def test_transitions_current_beat_id(self):
        from app.models import DirectorResponse
        b0, b1 = _make_beat(0, "b0"), _make_beat(1, "b1")
        sc = SCENARIO.model_copy(update={"beats": [b0, b1]})
        save = _fixture_save(current_beat_id="b0", sandbox_mode=False)
        dr = DirectorResponse(
            speaker_character_id="bram",
            dm_should_narrate=True,
            beat_transition=True,
            next_beat_id="b1",
            direction_note="",
        )
        result = apply_beat_transition(save, sc, dr)
        assert result == {"new_beat_id": "b1", "new_beat_name": "Beat 1"}
        assert save.current_beat_id == "b1"

    def test_appends_starter_prompt_as_dm_message(self):
        from app.models import DirectorResponse
        b0, b1 = _make_beat(0, "b0"), _make_beat(1, "b1")
        sc = SCENARIO.model_copy(update={"beats": [b0, b1]})
        save = _fixture_save(current_beat_id="b0", sandbox_mode=False)
        msg_count_before = len(save.messages)
        dr = DirectorResponse(
            speaker_character_id="bram",
            dm_should_narrate=True,
            beat_transition=True,
            next_beat_id="b1",
            direction_note="",
        )
        apply_beat_transition(save, sc, dr)
        assert len(save.messages) == msg_count_before + 1
        new_msg = save.messages[-1]
        assert new_msg.role == "dm"
        assert b1.starter_prompt in new_msg.content
        assert new_msg.beat_id_at_time == "b1"

    def test_invalid_next_beat_id_returns_none(self):
        from app.models import DirectorResponse
        b0 = _make_beat(0, "b0")
        sc = SCENARIO.model_copy(update={"beats": [b0]})
        save = _fixture_save(current_beat_id="b0")
        dr = DirectorResponse(
            speaker_character_id="bram",
            dm_should_narrate=True,
            beat_transition=True,
            next_beat_id="no-such-beat",
            direction_note="",
        )
        result = apply_beat_transition(save, sc, dr)
        assert result is None

    def test_beat_skip_accepted(self):
        """Director can skip beat 1 and jump directly to beat 2."""
        from app.models import DirectorResponse
        b0, b1, b2 = _make_beat(0, "b0"), _make_beat(1, "b1"), _make_beat(2, "b2")
        sc = SCENARIO.model_copy(update={"beats": [b0, b1, b2]})
        save = _fixture_save(current_beat_id="b0", sandbox_mode=False)
        dr = DirectorResponse(
            speaker_character_id="bram",
            dm_should_narrate=True,
            beat_transition=True,
            next_beat_id="b2",
            direction_note="",
        )
        result = apply_beat_transition(save, sc, dr)
        assert result is not None
        assert save.current_beat_id == "b2"


# ── Phase 2: run_phase2 ───────────────────────────────────────────────────────

class TestRunPhase2:

    async def test_emits_tokens_and_message_complete(self):
        from app.models import DirectorResponse
        save = _fixture_save()
        dr = DirectorResponse(
            speaker_character_id="bram",
            dm_should_narrate=False,
            beat_transition=False,
            next_beat_id=None,
            direction_note="",
        )
        async def mock_stream(model, messages, **kwargs):
            for token in ["Hello", " ", "there"]:
                yield token
        with patch("app.phases.stream_chat", side_effect=mock_stream):
            events = await _collect_phase2(save, SCENARIO, CHARACTERS, dr)
        token_events = [e for e in events if e["event"] == "token"]
        complete_events = [e for e in events if e["event"] == "message_complete"]
        assert len(token_events) == 3
        assert len(complete_events) == 1
        assert complete_events[0]["character_id"] == "bram"

    async def test_dm_narrates_first_then_character(self):
        # Verify that when both DM narration and a character response occur,
        # the DM message is appended to save.messages before the character message.
        from app.models import DirectorResponse
        save = _fixture_save()
        initial_count = len(save.messages)
        dr = DirectorResponse(
            speaker_character_id="bram",
            dm_should_narrate=True,
            beat_transition=False,
            next_beat_id=None,
            direction_note="Describe the dungeon.",
        )
        async def mock_stream(model, messages, **kwargs):
            yield "Some response text."
        with patch("app.phases.stream_chat", side_effect=mock_stream):
            await _collect_phase2(save, SCENARIO, CHARACTERS, dr)
        # Two new messages appended: DM narration first, then character response
        new_messages = save.messages[initial_count:]
        assert len(new_messages) == 2
        assert new_messages[0].role == "dm"
        assert new_messages[1].role == "character"
        assert new_messages[1].character_id == "bram"

    async def test_null_speaker_skips_chosen_speaker(self):
        # Director chose narration only; no character should speak after.
        # Phase 1.5 makes one draft call, Phase 2 makes the DM narration call = 2 total.
        from app.models import DirectorResponse
        save = _fixture_save()
        initial_count = len(save.messages)
        dr = DirectorResponse(
            speaker_character_id=None,
            dm_should_narrate=True,
            beat_transition=False,
            next_beat_id=None,
            direction_note="Set the scene.",
        )
        call_count = 0
        async def mock_stream(model, messages, **kwargs):
            nonlocal call_count
            call_count += 1
            yield "The torches gutter as silence settles over the chamber."
        with patch("app.phases.stream_chat", side_effect=mock_stream):
            events = await _collect_phase2(save, SCENARIO, CHARACTERS, dr)
        # Two stream calls: one director draft + one DM narration.
        assert call_count == 2
        # Exactly one new message (the DM narration), not two.
        assert len(save.messages) == initial_count + 1
        assert save.messages[-1].role == "dm"
        # No `notice` event — narration happened.
        assert not any(e["event"] == "notice" for e in events)

    async def test_null_speaker_no_narration_emits_notice(self):
        # Pathological director output: no speaker AND no narration.
        # Should produce no content but emit a notice and not raise.
        from app.models import DirectorResponse
        save = _fixture_save()
        initial_count = len(save.messages)
        dr = DirectorResponse(
            speaker_character_id=None,
            dm_should_narrate=False,
            beat_transition=False,
            next_beat_id=None,
            direction_note="",
        )
        call_count = 0
        async def mock_stream(model, messages, **kwargs):
            nonlocal call_count
            call_count += 1
            yield "should not be called"
        with patch("app.phases.stream_chat", side_effect=mock_stream):
            events = await _collect_phase2(save, SCENARIO, CHARACTERS, dr)
        assert call_count == 0
        assert len(save.messages) == initial_count
        notices = [e for e in events if e["event"] == "notice"]
        assert len(notices) == 1
        assert notices[0]["level"] == "warning"
        assert "nothing" in notices[0]["message"].lower()

    async def test_messages_appended_to_save(self):
        from app.models import DirectorResponse
        save = _fixture_save()
        initial_count = len(save.messages)
        dr = DirectorResponse(
            speaker_character_id="bram",
            dm_should_narrate=False,
            beat_transition=False,
            next_beat_id=None,
            direction_note="",
        )
        async def mock_stream(model, messages, **kwargs):
            yield "Aye, lad, this is different enough."
        with patch("app.phases.stream_chat", side_effect=mock_stream):
            await _collect_phase2(save, SCENARIO, CHARACTERS, dr)
        assert len(save.messages) == initial_count + 1
        assert save.messages[-1].role == "character"
        assert save.messages[-1].character_id == "bram"


# ── Phase 3: run_phase3 ───────────────────────────────────────────────────────

async def _mock_draft_stream(model, messages, **kwargs):
    """Shared mock for stream_chat that returns a simple context brief."""
    yield "The player stands at the dungeon entrance, torch in hand."


class TestRunPhase3:

    async def test_returns_four_options(self):
        raw = {"options": [
            {"text": "Wait.", "advances_beat": False, "dice_roll": None},
            {"text": "Run.", "advances_beat": False, "dice_roll": None},
            {"text": "Fight.", "advances_beat": True, "dice_roll": None},
            {"text": "Hide.", "advances_beat": False, "dice_roll": None},
        ]}
        with patch("app.phases.stream_chat", side_effect=_mock_draft_stream), \
             patch("app.phases.structured_chat", new=AsyncMock(return_value=raw)):
            options, context = await run_phase3(_fixture_save(), SCENARIO, CHARACTERS)
        assert len(options) == 4
        assert options[0] == {"text": "Wait.", "advances_beat": False, "dice_roll": None}
        assert options[2] == {"text": "Fight.", "advances_beat": True, "dice_roll": None}
        assert isinstance(context, str)

    async def test_falls_back_on_exhaustion(self):
        async def mock_structured(model, messages, schema, **kwargs):
            return {"options": [{"text": "only one", "advances_beat": False, "dice_roll": None}]}  # always invalid
        with patch("app.phases.stream_chat", side_effect=_mock_draft_stream), \
             patch("app.phases.structured_chat", side_effect=mock_structured):
            options, _ = await run_phase3(_fixture_save(), SCENARIO, CHARACTERS)
        assert options == list(OPTIONS_FALLBACK)

    async def test_returns_fallback_when_no_dm(self):
        save = _fixture_save(active_character_ids=["bram"])  # no DM
        chars = {BRAM.id: BRAM}
        options, context = await run_phase3(save, SCENARIO, chars)
        assert options == list(OPTIONS_FALLBACK)
        assert context == ""

    async def test_accepts_direction_note_parameter(self):
        raw = {"options": [
            {"text": "A.", "advances_beat": False, "dice_roll": None},
            {"text": "B.", "advances_beat": False, "dice_roll": None},
            {"text": "C.", "advances_beat": False, "dice_roll": None},
            {"text": "D.", "advances_beat": False, "dice_roll": None},
        ]}
        with patch("app.phases.stream_chat", side_effect=_mock_draft_stream), \
             patch("app.phases.structured_chat", new=AsyncMock(return_value=raw)):
            options, _ = await run_phase3(_fixture_save(), SCENARIO, CHARACTERS, direction_note="The scene shifts.")
        assert [o["text"] for o in options] == ["A.", "B.", "C.", "D."]


# ── Full turn integration ─────────────────────────────────────────────────────

class TestFullTurnIntegration:

    async def test_happy_path_director_phase2_phase3(self):
        save = _fixture_save()
        initial_count = len(save.messages)

        director_raw = _valid_director_dict("bram")
        options_raw = {"options": [
            {"text": "A", "advances_beat": False, "dice_roll": None},
            {"text": "B", "advances_beat": False, "dice_roll": None},
            {"text": "C", "advances_beat": False, "dice_roll": None},
            {"text": "D", "advances_beat": False, "dice_roll": None},
        ]}

        async def mock_structured(model, messages, schema, **kwargs):
            if "dm_should_narrate" in json.dumps(schema):
                return director_raw
            return options_raw

        async def mock_stream(model, messages, **kwargs):
            yield "Hello, this is a unique dungeon scene response."

        with patch("app.phases.structured_chat", side_effect=mock_structured), \
             patch("app.phases.stream_chat", side_effect=mock_stream):
            dr = await run_director(save, SCENARIO, CHARACTERS)
            bt = apply_beat_transition(save, SCENARIO, dr)
            events = await _collect_phase2(save, SCENARIO, CHARACTERS, dr)
            options, _ = await run_phase3(save, SCENARIO, CHARACTERS, direction_note=dr.direction_note)

        assert dr.speaker_character_id == "bram"
        assert bt is None  # no beats in fixture
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) >= 1
        assert [o["text"] for o in options] == ["A", "B", "C", "D"]
        assert len(save.messages) > initial_count

    async def test_two_beat_transition_flow(self):
        b0, b1 = _make_beat(0, "b0"), _make_beat(1, "b1")
        sc = SCENARIO.model_copy(update={"beats": [b0, b1]})
        save = _fixture_save(current_beat_id="b0", sandbox_mode=False)

        director_raw = _valid_director_dict("bram", beat_transition=True, next_beat_id="b1")

        with patch("app.phases.structured_chat", new=AsyncMock(return_value=director_raw)):
            dr = await run_director(save, sc, CHARACTERS)

        assert dr.beat_transition is True
        assert dr.next_beat_id == "b1"

        bt_data = apply_beat_transition(save, sc, dr)
        assert bt_data == {"new_beat_id": "b1", "new_beat_name": "Beat 1"}
        assert save.current_beat_id == "b1"
        # starter_prompt appended as DM message
        last_msg = save.messages[-1]
        assert last_msg.role == "dm"
        assert last_msg.beat_id_at_time == "b1"

    async def test_backward_beat_rejected_fallback_used(self):
        b0, b1 = _make_beat(0, "b0"), _make_beat(1, "b1")
        sc = SCENARIO.model_copy(update={"beats": [b0, b1]})
        save = _fixture_save(current_beat_id="b1", sandbox_mode=False)
        # Director always tries to go backward to b0
        director_raw = _valid_director_dict("bram", beat_transition=True, next_beat_id="b0")

        with patch("app.phases.structured_chat", new=AsyncMock(return_value=director_raw)):
            dr = await run_director(save, sc, CHARACTERS)

        # Validation fails, fallback applied — no beat_transition
        assert dr.beat_transition is False
        bt_data = apply_beat_transition(save, sc, dr)
        assert bt_data is None
        assert save.current_beat_id == "b1"  # unchanged
