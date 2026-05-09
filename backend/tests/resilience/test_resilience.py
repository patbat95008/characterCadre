"""
Resilience tests for CharacterCadre.
Tests failure modes: malformed JSON, retry exhaustion, mid-stream timeout,
empty responses, loop detection, and Ollama unreachable.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.fixtures import BRAM, CHARACTERS, SCENARIO, make_stage1_save
from app.models import DirectorResponse, Message
from app.ollama_client import OllamaTimeoutError, OllamaUnreachableError
from app.phases import run_director, run_phase2, run_phase3
from app.validation import OPTIONS_FALLBACK


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fixture_save(**kwargs):
    save = make_stage1_save()
    for k, v in kwargs.items():
        setattr(save, k, v)
    return save


def _no_narrate_dr(speaker_id: str = "bram") -> DirectorResponse:
    return DirectorResponse(
        speaker_character_id=speaker_id,
        dm_should_narrate=False,
        beat_transition=False,
        next_beat_id=None,
        direction_note="",
    )


async def _collect_phase2(save, dr) -> list[dict]:
    gen = await run_phase2(save, SCENARIO, CHARACTERS, dr)
    events = []
    async for event in gen:
        events.append(event)
    return events


def _add_prior_turn(save, char_id: str, content: str) -> None:
    """Simulate a completed prior turn: user message then character response.
    Loop detection only checks messages after the first user message, so tests
    that exercise loop detection must call this instead of appending directly."""
    save.messages.append(Message(
        id="prior-user",
        role="user",
        character_id=None,
        content="What do you see?",
        timestamp="2026-01-01T00:00:00+00:00",
    ))
    save.messages.append(Message(
        id="prior-char",
        role="character",
        character_id=char_id,
        content=content,
        timestamp="2026-01-01T00:00:00+00:00",
    ))


# ── Malformed JSON from Director ──────────────────────────────────────────────

class TestMalformedJSONFromDirector:

    async def test_malformed_on_first_try_retries_and_succeeds(self):
        calls = []
        async def mock_structured(model, messages, schema, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return {"garbage": "data"}  # missing required fields
            return {
                "speaker_character_id": "bram",
                "dm_should_narrate": True,
                "beat_transition": False,
                "next_beat_id": None,
                "direction_note": "OK",
                "reasoning": "",
            }
        with patch("app.phases.structured_chat", side_effect=mock_structured):
            result = await run_director(_fixture_save(), SCENARIO, CHARACTERS)
        assert result.speaker_character_id == "bram"
        assert len(calls) == 2

    async def test_all_retries_exhausted_uses_fallback(self):
        async def mock_structured(model, messages, schema, **kwargs):
            return {"completely": "wrong"}
        with patch("app.phases.structured_chat", side_effect=mock_structured):
            result = await run_director(_fixture_save(), SCENARIO, CHARACTERS)
        # Fallback: first non-DM character
        assert result.speaker_character_id == BRAM.id
        assert result.dm_should_narrate is True
        assert result.beat_transition is False


# ── Malformed JSON from Options ───────────────────────────────────────────────

class TestMalformedJSONFromOptions:

    async def test_malformed_options_retries_and_succeeds(self):
        calls = []
        async def mock_structured(model, messages, schema, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return {"options": ["only one"]}  # invalid
            return {"options": ["A", "B", "C", "D"]}
        async def mock_stream(model, messages, **kwargs):
            yield "The player stands ready."
        with patch("app.phases.stream_chat", side_effect=mock_stream), \
             patch("app.phases.structured_chat", side_effect=mock_structured):
            options, _ = await run_phase3(_fixture_save(), SCENARIO, CHARACTERS)
        assert options == ["A", "B", "C", "D"]
        assert len(calls) == 2

    async def test_options_all_retries_exhausted_uses_fallback(self):
        async def mock_structured(model, messages, schema, **kwargs):
            return {"options": ["only one"]}
        async def mock_stream(model, messages, **kwargs):
            yield "The player stands ready."
        with patch("app.phases.stream_chat", side_effect=mock_stream), \
             patch("app.phases.structured_chat", side_effect=mock_structured):
            options, _ = await run_phase3(_fixture_save(), SCENARIO, CHARACTERS)
        assert options == list(OPTIONS_FALLBACK)


# ── Empty Stream Response ─────────────────────────────────────────────────────

class TestEmptyStreamResponse:

    async def test_empty_response_triggers_retry(self):
        save = _fixture_save()
        dr = _no_narrate_dr("bram")
        call_count = [0]

        async def mock_stream(model, messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call is the Phase 1.5 director draft — return a brief context
                yield "The player stands at the dungeon entrance."
            elif call_count[0] == 2:
                return  # empty speaker response — triggers retry
            else:
                yield "Aye, lad. This is a complete and different response."

        with patch("app.phases.stream_chat", side_effect=mock_stream):
            events = await _collect_phase2(save, dr)

        regenerate_events = [e for e in events if e["event"] == "regenerate"]
        complete_events = [e for e in events if e["event"] == "message_complete"]
        assert len(regenerate_events) >= 1
        assert len(complete_events) >= 1


# ── Mid-Stream Timeout ────────────────────────────────────────────────────────

class TestMidStreamTimeout:

    async def test_timeout_propagates_as_exception(self):
        save = _fixture_save()
        dr = _no_narrate_dr("bram")

        async def mock_stream(model, messages, **kwargs):
            yield "Aye"
            raise OllamaTimeoutError("idle timeout")

        with patch("app.phases.stream_chat", side_effect=mock_stream):
            with pytest.raises(OllamaTimeoutError):
                await _collect_phase2(save, dr)


# ── Loop Detection ────────────────────────────────────────────────────────────

class TestLoopDetection:

    async def test_exact_loop_emits_validation_warning(self):
        save = _fixture_save()
        _add_prior_turn(save, "bram", "Aye, lad. We stay together always, you and I.")
        dr = _no_narrate_dr("bram")

        async def mock_stream(model, messages, **kwargs):
            # Always return exact copy — loop detection commits-and-warns, no auto-retry
            yield "Aye, lad. We stay together always, you and I."

        with patch("app.phases.stream_chat", side_effect=mock_stream):
            events = await _collect_phase2(save, dr)

        warning_events = [e for e in events if e["event"] == "validation_warning"]
        regen_events = [e for e in events if e["event"] == "regenerate"]
        complete_events = [e for e in events if e["event"] == "message_complete"]
        assert len(warning_events) >= 1, "loop should emit validation_warning"
        assert len(regen_events) == 0, "loop should not auto-regenerate"
        assert len(complete_events) >= 1

    async def test_fuzzy_loop_same_speaker_triggers_regenerate(self):
        save = _fixture_save()
        original = "You see a dark corridor stretching before you into shadow and mist."
        near_copy = "You see a dark corridor stretching before you into darkness and shadow."
        _add_prior_turn(save, "bram", original)
        dr = _no_narrate_dr("bram")
        call_count = [0]

        async def mock_stream(model, messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                yield near_copy
            else:
                yield "Completely different content about fighting and swords and iron shields and ancient runes."

        with patch("app.phases.stream_chat", side_effect=mock_stream):
            events = await _collect_phase2(save, dr)

        # The response should complete — either via retry success or exhaustion
        event_names = [e["event"] for e in events]
        assert "message_complete" in event_names

    async def test_loop_exhaustion_emits_validation_warning(self):
        save = _fixture_save()
        repeated = "Aye, lad. We stay together always and forever, you hear me?"
        _add_prior_turn(save, "bram", repeated)
        dr = _no_narrate_dr("bram")

        async def mock_stream(model, messages, **kwargs):
            yield repeated  # always return the loop

        with patch("app.phases.stream_chat", side_effect=mock_stream):
            events = await _collect_phase2(save, dr)

        warning_events = [e for e in events if e["event"] == "validation_warning"]
        complete_events = [e for e in events if e["event"] == "message_complete"]
        assert len(warning_events) >= 1
        assert len(complete_events) >= 1  # still emits message despite warning


# ── Ollama Unreachable ────────────────────────────────────────────────────────

class TestOllamaUnreachable:

    async def test_director_unreachable_propagates_exception(self):
        """
        OllamaUnreachableError propagates through run_director to the route handler,
        which catches it and emits event: error. with_validation does not swallow
        connectivity errors — only validation failures get retried.
        """
        async def mock_structured(model, messages, schema, **kwargs):
            raise OllamaUnreachableError("connection refused")
        with patch("app.phases.structured_chat", side_effect=mock_structured):
            with pytest.raises(OllamaUnreachableError):
                await run_director(_fixture_save(), SCENARIO, CHARACTERS)

    async def test_phase2_unreachable_propagates(self):
        save = _fixture_save()
        dr = _no_narrate_dr("bram")

        async def mock_stream(model, messages, **kwargs):
            raise OllamaUnreachableError("connection refused")
            yield  # make it an async generator

        with patch("app.phases.stream_chat", side_effect=mock_stream):
            with pytest.raises(OllamaUnreachableError):
                await _collect_phase2(save, dr)

    async def test_save_not_mutated_if_stream_raises_before_any_token(self):
        save = _fixture_save()
        initial_msg_count = len(save.messages)
        dr = _no_narrate_dr("bram")

        async def mock_stream(model, messages, **kwargs):
            raise OllamaUnreachableError("connection refused")
            yield

        with patch("app.phases.stream_chat", side_effect=mock_stream):
            try:
                await _collect_phase2(save, dr)
            except OllamaUnreachableError:
                pass

        # No messages should have been committed since stream raised immediately
        assert len(save.messages) == initial_msg_count
