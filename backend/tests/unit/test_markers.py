"""
Marker format tests.

emit() and parse() are the contract between the app and the playtest harness,
so they are tested as a round trip: whatever the app writes, the harness must
be able to read back — including values containing the separator, newlines, or
enough text to trip truncation.
"""
from __future__ import annotations

import logging

from app import markers
from app.turn_context import current_turn_id


def _emitted(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == markers.MARKER_LOGGER_NAME]


class TestRoundTrip:

    def test_emit_parses_back_to_the_same_fields(self, caplog):
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            markers.emit("beat.advance", to_id="beat-two", trigger="player", to_order=1)

        lines = _emitted(caplog)
        assert len(lines) == 1
        parsed = markers.parse(lines[0])
        assert parsed is not None
        assert parsed.event == "beat.advance"
        assert parsed.fields == {"to_id": "beat-two", "trigger": "player", "to_order": "1"}
        assert parsed.number("to_order") == 1

    def test_turn_id_comes_from_the_ambient_context(self, caplog):
        token = current_turn_id.set("abcd1234")
        try:
            with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
                markers.emit("turn.start", save="s1")
        finally:
            current_turn_id.reset(token)

        parsed = markers.parse(_emitted(caplog)[0])
        assert parsed is not None
        assert parsed.turn == "abcd1234"
        assert parsed.get("save") == "s1"

    def test_absent_turn_id_parses_as_empty(self, caplog):
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            markers.emit("options.generated", count=4)
        parsed = markers.parse(_emitted(caplog)[0])
        assert parsed is not None
        assert parsed.turn == ""

    def test_booleans_and_none_render_predictably(self, caplog):
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            markers.emit("director.decision", dm_narrate=True, beat_transition=False, speaker=None)
        parsed = markers.parse(_emitted(caplog)[0])
        assert parsed is not None
        assert parsed.flag("dm_narrate") is True
        assert parsed.flag("beat_transition") is False
        assert parsed.get("speaker") == "-"


class TestSanitization:

    def test_separator_in_a_value_does_not_break_parsing(self, caplog):
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            markers.emit("validation.failed", reason="a|b|c", call="director")
        parsed = markers.parse(_emitted(caplog)[0])
        assert parsed is not None
        assert parsed.get("reason") == "a/b/c"
        assert parsed.get("call") == "director"

    def test_newlines_are_collapsed_so_a_marker_stays_one_line(self, caplog):
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            markers.emit("director.decision", note="line one\nline two\r\nthree")
        line = _emitted(caplog)[0]
        assert "\n" not in line and "\r" not in line
        parsed = markers.parse(line)
        assert parsed is not None
        assert parsed.get("note") == "line one line two  three"

    def test_long_values_are_truncated(self, caplog):
        with caplog.at_level(logging.INFO, logger=markers.MARKER_LOGGER_NAME):
            markers.emit("director.decision", note="x" * 500)
        parsed = markers.parse(_emitted(caplog)[0])
        assert parsed is not None
        note = parsed.get("note") or ""
        assert len(note) == markers.MAX_VALUE_LEN
        assert note.endswith("…")


class TestParse:

    def test_non_marker_lines_are_ignored(self):
        assert markers.parse("turn completed (save=abc, duration=1200ms)") is None
        assert markers.parse("") is None
        assert markers.parse("CCM") is None

    def test_as_dict_flattens_for_jsonl(self):
        parsed = markers.parse("CCM|beat.advance|turn=t1|to_id=b2|trigger=player")
        assert parsed is not None
        assert parsed.as_dict() == {
            "event": "beat.advance", "turn": "t1", "to_id": "b2", "trigger": "player",
        }
