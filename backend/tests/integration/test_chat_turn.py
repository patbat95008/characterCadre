"""
Integration tests for the /api/chat/turn SSE stream.

These cover behaviors layered on top of run_director / run_phase2 / run_phase3:
- Player `beat_advance` flag overriding the Director's `next_beat_id` is logged.
- Final-beat completion emits exactly one `ending_reached` event (no parallel
  `beat_transition`), with the final beat info merged into its payload.
- Non-final beat transitions continue to emit `beat_transition` alone.

Ollama is mocked at the phases boundary (structured_chat + stream_chat) so the
suite runs offline.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from app import markers, storage
from app.fixtures import BRAM, NARRATOR, SCENARIO

# isolated_data_dir (autouse) and client come from tests/conftest.py


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scenario_with_beats(*beat_specs: tuple[str, int]):
    """Persist SCENARIO with the given (id, order) beats and return it."""
    from app.models import Beat
    beats = [
        Beat(
            id=bid,
            order=order,
            name=f"Beat {bid}",
            description="",
            summary="",
            summary_hash="",
            transition_condition="",
            starter_prompt=f"You arrive at {bid}.",
        )
        for bid, order in beat_specs
    ]
    scenario = SCENARIO.model_copy(update={"beats": beats})
    storage.save_scenario(scenario)
    return scenario


def _make_save_with_beat(current_beat_id: str | None):
    from datetime import datetime, timezone
    from app.models import Message, Save
    now = datetime.now(timezone.utc).isoformat()
    save = Save(
        id="test-save-1",
        scenario_id=SCENARIO.id,
        name="Test save",
        active_character_ids=[NARRATOR.id, BRAM.id],
        user_name="Alice",
        current_beat_id=current_beat_id,
        sandbox_mode=False,
        messages=[Message(
            id="m0", role="dm", character_id=None,
            content="An adventure begins.", timestamp=now,
        )],
        created_at=now,
        updated_at=now,
    )
    storage.save_save(save)
    return save


def _director_raw(speaker_id: str | None, *, beat_transition: bool = False, next_beat_id: str | None = None) -> dict:
    return {
        "speaker_character_id": speaker_id,
        "dm_should_narrate": True,
        "beat_transition": beat_transition,
        "next_beat_id": next_beat_id,
        "direction_note": "Move the scene.",
        "reasoning": "Test.",
    }


def _options_raw() -> dict:
    return {"options": [
        {"text": "A.", "advances_beat": False, "dice_roll": None},
        {"text": "B.", "advances_beat": False, "dice_roll": None},
        {"text": "C.", "advances_beat": False, "dice_roll": None},
        {"text": "D.", "advances_beat": False, "dice_roll": None},
    ]}


def _parse_sse(text: str) -> list[tuple[str, Any]]:
    """Parse an SSE body into [(event_name, data_dict_or_str), ...]."""
    events: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:]
        try:
            parsed: Any = json.loads(data)
        except (ValueError, json.JSONDecodeError):
            parsed = data
        events.append((name, parsed))
    return events


async def _post_turn(
    client: AsyncClient,
    *,
    beat_advance: bool = False,
    dice_roll: dict | None = None,
) -> str:
    """Drive a full /api/chat/turn and return the raw SSE response body."""
    body: dict[str, Any] = {
        "user_message": "I look around.",
        "save_id": "test-save-1",
        "beat_advance": beat_advance,
    }
    if dice_roll:
        body["dice_roll"] = dice_roll
    chunks: list[str] = []
    async with client.stream("POST", "/api/chat/turn", json=body) as r:
        assert r.status_code == 200, await r.aread()
        async for chunk in r.aiter_text():
            chunks.append(chunk)
    return "".join(chunks)


def _markers(caplog) -> list:
    """Parse the marker records captured during a turn."""
    out = []
    for record in caplog.records:
        if record.name != markers.MARKER_LOGGER_NAME:
            continue
        parsed = markers.parse(record.getMessage())
        if parsed is not None:
            out.append(parsed)
    return out


def _marker_events(caplog) -> list[str]:
    return [m.event for m in _markers(caplog)]


def _mock_llm(director_raw: dict):
    """Patch both Ollama seams for one turn."""
    async def mock_stream(model, messages, **kwargs):
        yield "Some text."

    return (
        patch("app.phases.structured_chat", new=AsyncMock(
            side_effect=lambda model, msgs, schema, **kw: (
                director_raw
                if "dm_should_narrate" in json.dumps(schema)
                else _options_raw()
            ),
        )),
        patch("app.phases.stream_chat", side_effect=mock_stream),
    )


# ── Tests ────────────────────────────────────────────────────────────────────

class TestEndingEvent:

    async def test_final_beat_completion_emits_single_ending_event(self, client):
        # Two beats; save is on the last (b2). Use the player-flag path with
        # beat_advance=True — that's how endings reach is_final_beat_completion
        # in practice (the validator blocks Director-issued "stay on last beat"
        # responses).
        _scenario_with_beats(("b1", 0), ("b2", 1))
        _make_save_with_beat("b2")

        async def mock_stream(model, messages, **kwargs):
            yield "Some text."

        with patch("app.phases.structured_chat", new=AsyncMock(
            side_effect=lambda model, msgs, schema, **kw: (
                _director_raw(BRAM.id)
                if "dm_should_narrate" in json.dumps(schema)
                else _options_raw()
            ),
        )), patch("app.phases.stream_chat", side_effect=mock_stream):
            body = await _post_turn(client, beat_advance=True)

        events = _parse_sse(body)
        ending_events = [e for e in events if e[0] == "ending_reached"]
        beat_events = [e for e in events if e[0] == "beat_transition"]
        assert len(ending_events) == 1, f"want 1 ending_reached, got {len(ending_events)}"
        assert len(beat_events) == 0, f"want 0 beat_transition on final-beat ending, got {len(beat_events)}"
        payload = ending_events[0][1]
        assert payload.get("sandbox_mode") is True

    async def test_non_final_beat_transition_emits_beat_transition_only(self, client):
        # Three beats; save is on b1. Director advances to b2 (not the last).
        _scenario_with_beats(("b1", 0), ("b2", 1), ("b3", 2))
        _make_save_with_beat("b1")

        async def mock_stream(model, messages, **kwargs):
            yield "Some text."

        with patch("app.phases.structured_chat", new=AsyncMock(
            side_effect=lambda model, msgs, schema, **kw: (
                _director_raw(BRAM.id, beat_transition=True, next_beat_id="b2")
                if "dm_should_narrate" in json.dumps(schema)
                else _options_raw()
            ),
        )), patch("app.phases.stream_chat", side_effect=mock_stream):
            body = await _post_turn(client)

        events = _parse_sse(body)
        beat_events = [e for e in events if e[0] == "beat_transition"]
        ending_events = [e for e in events if e[0] == "ending_reached"]
        assert len(beat_events) == 1
        assert beat_events[0][1]["new_beat_id"] == "b2"
        assert len(ending_events) == 0


class TestBeatOverrideLogging:

    async def test_player_override_disagrees_with_director_logs_warning(self, client, caplog):
        # Save on b1; player flag is set AND director picked a DIFFERENT next beat (b3).
        # Player's choice silently rewrites to find_next_beat() result (b2);
        # the chat route should log the disagreement.
        _scenario_with_beats(("b1", 0), ("b2", 1), ("b3", 2))
        _make_save_with_beat("b1")

        async def mock_stream(model, messages, **kwargs):
            yield "Some text."

        with caplog.at_level(logging.INFO, logger="app.routes.chat"):
            with patch("app.phases.structured_chat", new=AsyncMock(
                side_effect=lambda model, msgs, schema, **kw: (
                    _director_raw(BRAM.id, beat_transition=True, next_beat_id="b3")
                    if "dm_should_narrate" in json.dumps(schema)
                    else _options_raw()
                ),
            )), patch("app.phases.stream_chat", side_effect=mock_stream):
                await _post_turn(client, beat_advance=True)

        override_logs = [
            r for r in caplog.records
            if "overrides Director's choice" in r.getMessage()
        ]
        assert len(override_logs) == 1, (
            f"want exactly one override log, got {len(override_logs)}: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        msg = override_logs[0].getMessage()
        assert "director_next=b3" in msg
        assert "player_next=b2" in msg

    async def test_player_override_agreeing_with_director_does_not_log(self, client, caplog):
        # Director and player both point at b2 — no disagreement, no log.
        _scenario_with_beats(("b1", 0), ("b2", 1), ("b3", 2))
        _make_save_with_beat("b1")

        async def mock_stream(model, messages, **kwargs):
            yield "Some text."

        with caplog.at_level(logging.INFO, logger="app.routes.chat"):
            with patch("app.phases.structured_chat", new=AsyncMock(
                side_effect=lambda model, msgs, schema, **kw: (
                    _director_raw(BRAM.id, beat_transition=True, next_beat_id="b2")
                    if "dm_should_narrate" in json.dumps(schema)
                    else _options_raw()
                ),
            )), patch("app.phases.stream_chat", side_effect=mock_stream):
                await _post_turn(client, beat_advance=True)

        override_logs = [
            r for r in caplog.records
            if "overrides Director's choice" in r.getMessage()
        ]
        assert override_logs == []


class TestTurnMarkers:
    """The playtest harness reads these markers to decide whether the LLM did
    what was asked. Assert the contract here, offline, so a live run failing
    means the *model* misbehaved rather than the instrumentation."""

    async def test_turn_emits_the_expected_marker_sequence(self, client, caplog):
        _scenario_with_beats(("b1", 0), ("b2", 1))
        _make_save_with_beat("b1")

        structured, stream = _mock_llm(_director_raw(BRAM.id))
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with structured, stream:
                await _post_turn(client)

        events = _marker_events(caplog)
        for expected in (
            "turn.start", "director.decision", "speaker.plan",
            "speaker.commit", "options.generated", "turn.end",
        ):
            assert expected in events, f"missing {expected} marker; got {events}"
        assert events[0] == "turn.start"
        assert events[-1] == "turn.end"

    async def test_all_markers_in_a_turn_share_one_turn_id(self, client, caplog):
        _scenario_with_beats(("b1", 0), ("b2", 1))
        _make_save_with_beat("b1")

        structured, stream = _mock_llm(_director_raw(BRAM.id))
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with structured, stream:
                await _post_turn(client)

        turn_ids = {m.turn for m in _markers(caplog)}
        assert len(turn_ids) == 1, f"expected one turn id, got {turn_ids}"
        assert turn_ids.pop(), "turn id should not be empty inside a turn"

    async def test_player_advance_marks_trigger_as_player(self, client, caplog):
        _scenario_with_beats(("b1", 0), ("b2", 1), ("b3", 2))
        _make_save_with_beat("b1")

        structured, stream = _mock_llm(_director_raw(BRAM.id))
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with structured, stream:
                await _post_turn(client, beat_advance=True)

        advances = [m for m in _markers(caplog) if m.event == "beat.advance"]
        assert len(advances) == 1, f"want 1 beat.advance, got {len(advances)}"
        assert advances[0].get("trigger") == "player"
        assert advances[0].get("to_id") == "b2"

    async def test_director_advance_marks_trigger_as_director(self, client, caplog):
        _scenario_with_beats(("b1", 0), ("b2", 1), ("b3", 2))
        _make_save_with_beat("b1")

        structured, stream = _mock_llm(
            _director_raw(BRAM.id, beat_transition=True, next_beat_id="b2")
        )
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with structured, stream:
                await _post_turn(client)

        advances = [m for m in _markers(caplog) if m.event == "beat.advance"]
        assert len(advances) == 1
        assert advances[0].get("trigger") == "director"

    async def test_failed_roll_blocks_the_advance_and_says_why(self, client, caplog, monkeypatch):
        # A failed skill check makes beat_advance ineligible. Without a marker
        # the harness cannot tell "correctly withheld" from "silently dropped".
        from app import dice as dice_module
        monkeypatch.setattr(dice_module.random, "randint", lambda a, b: 2)

        _scenario_with_beats(("b1", 0), ("b2", 1))
        _make_save_with_beat("b1")

        structured, stream = _mock_llm(_director_raw(BRAM.id))
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with structured, stream:
                await _post_turn(
                    client,
                    beat_advance=True,
                    dice_roll={"dice": "D20", "difficulty": "Hard"},
                )

        captured = _markers(caplog)
        blocked = [m for m in captured if m.event == "beat.blocked"]
        assert len(blocked) == 1, f"want 1 beat.blocked, got {[m.event for m in captured]}"
        assert blocked[0].get("reason") == "dice_failure"
        assert blocked[0].get("outcome") == "failure"
        assert not [m for m in captured if m.event == "beat.advance"]

        rolls = [m for m in captured if m.event == "dice.roll"]
        assert len(rolls) == 1
        assert rolls[0].get("outcome") == "failure"
        assert rolls[0].number("value") == 2

    async def test_advance_flag_works_on_the_opening_turn(self, client, caplog):
        # current_beat_id starts null on a fresh save. find_next_beat() treats
        # that as "before the first beat" and returns it, so the player's toggle
        # works on turn one instead of silently doing nothing.
        _scenario_with_beats(("b1", 0), ("b2", 1))
        _make_save_with_beat(None)

        structured, stream = _mock_llm(_director_raw(BRAM.id))
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with structured, stream:
                await _post_turn(client, beat_advance=True)

        captured = _markers(caplog)
        assert not [m for m in captured if m.event == "beat.blocked"]
        advances = [m for m in captured if m.event == "beat.advance"]
        assert len(advances) == 1
        assert advances[0].get("to_id") == "b1"
        assert advances[0].get("trigger") == "player"

    async def test_advance_flag_on_a_beatless_scenario_reports_no_next_beat(self, client, caplog):
        # A scenario with no beats at all still has nowhere to go, and the
        # harness needs to see that rather than infer it from silence.
        scenario = SCENARIO.model_copy(update={"beats": []})
        storage.save_scenario(scenario)
        _make_save_with_beat(None)

        structured, stream = _mock_llm(_director_raw(BRAM.id))
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with structured, stream:
                await _post_turn(client, beat_advance=True)

        blocked = [m for m in _markers(caplog) if m.event == "beat.blocked"]
        assert len(blocked) == 1
        assert blocked[0].get("reason") == "no_next_beat"

    async def test_director_cannot_open_the_adventure_on_a_later_beat(self, client, caplog):
        # From a null current_beat_id the forward-order rule has nothing to
        # compare against, so the validator enforces "first transition goes to
        # the first beat" explicitly. Three rejected attempts, then fallback.
        _scenario_with_beats(("b1", 0), ("b2", 1), ("b3", 2))
        _make_save_with_beat(None)

        structured, stream = _mock_llm(
            _director_raw(BRAM.id, beat_transition=True, next_beat_id="b3")
        )
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with structured, stream:
                await _post_turn(client)

        captured = _markers(caplog)
        assert not [m for m in captured if m.event == "beat.advance"], (
            "the Director should not have been able to skip to b3"
        )
        failures = [m for m in captured if m.event == "validation.failed"]
        assert len(failures) == 3
        assert "skips ahead" in (failures[0].get("reason") or "")
        save = storage.get_save("test-save-1")
        assert save is not None and save.current_beat_id is None

    async def test_fallback_is_marked_when_the_director_never_validates(self, client, caplog):
        _scenario_with_beats(("b1", 0), ("b2", 1))
        _make_save_with_beat("b1")

        # speaker_character_id=None with dm_should_narrate=False is rejected by
        # the validator, so all three attempts fail and the fallback engages.
        structured, stream = _mock_llm({
            "speaker_character_id": None,
            "dm_should_narrate": False,
            "beat_transition": False,
            "next_beat_id": None,
            "direction_note": "",
            "reasoning": "",
        })
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with structured, stream:
                await _post_turn(client)

        captured = _markers(caplog)
        fallbacks = [m for m in captured if m.event == "validation.fallback"]
        assert [m.get("call") for m in fallbacks] == ["director"]
        failures = [m for m in captured if m.event == "validation.failed"]
        assert len(failures) == 3, "want one marker per failed attempt"
        assert failures[0].get("call") == "director"

    async def test_options_marker_reports_the_advancing_and_dice_flags(self, client, caplog):
        _scenario_with_beats(("b1", 0), ("b2", 1))
        _make_save_with_beat("b1")

        async def mock_stream(model, messages, **kwargs):
            yield "Some text."

        options_raw = {"options": [
            {"text": "A.", "advances_beat": False, "dice_roll": None},
            {"text": "B.", "advances_beat": True, "dice_roll": None},
            {"text": "C.", "advances_beat": False,
             "dice_roll": {"dice": "D20", "difficulty": "Medium"}},
            {"text": "D.", "advances_beat": False, "dice_roll": None},
        ]}

        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            with patch("app.phases.structured_chat", new=AsyncMock(
                side_effect=lambda model, msgs, schema, **kw: (
                    _director_raw(BRAM.id)
                    if "dm_should_narrate" in json.dumps(schema)
                    else options_raw
                ),
            )), patch("app.phases.stream_chat", side_effect=mock_stream):
                await _post_turn(client)

        generated = [m for m in _markers(caplog) if m.event == "options.generated"]
        assert len(generated) == 1
        assert generated[0].number("count") == 4
        assert generated[0].get("advance_idx") == "1"
        assert generated[0].get("dice_idx") == "2"
        assert generated[0].get("dice") == "D20"
        assert generated[0].get("difficulty") == "Medium"
