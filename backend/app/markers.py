"""
Structured log markers for programmatic playtesting.

The SSE stream tells a client *what happened*, but not *why*: it carries no
beat-advance trigger, no signal that a validation fallback engaged, no speaker
plan, and no per-call latency. Markers close that gap with one stable,
grep-able line per interesting event:

    CCM|beat.advance|turn=a1b2c3d4|from=The Entry Hall|to=The Trapped Corridor|trigger=player

They are emitted on their own `cc.marker` logger, separate from the prose logs,
which stay exactly as they were. Set CC_MARKERS=0 to keep them out of the
console; records are still produced so in-process test handlers can capture them.

`emit()` and `parse()` live together on purpose — the playtest harness reads
markers with the same code that writes them, so the format has one definition.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.turn_context import get_turn_id

MARKER_LOGGER_NAME = "cc.marker"
PREFIX = "CCM"
SEP = "|"
MAX_VALUE_LEN = 200

logger = logging.getLogger(MARKER_LOGGER_NAME)


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt(value: Any) -> str:
    """Render a field value as a single-line, separator-safe token."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # The separator and newlines would break parsing; swap rather than escape so
    # a marker line is always splittable on a plain SEP.
    text = text.replace(SEP, "/").replace("\r", " ").replace("\n", " ")
    if len(text) > MAX_VALUE_LEN:
        text = text[: MAX_VALUE_LEN - 1] + "…"
    return text


def _fmt_key(key: str) -> str:
    """Keys are code-controlled, but keep them parse-safe regardless."""
    return key.replace(SEP, "/").replace("=", "_")


def format_marker(event: str, turn: str, fields: dict[str, Any]) -> str:
    parts = [PREFIX, _fmt(event), f"turn={_fmt(turn or '-')}"]
    parts += [f"{_fmt_key(k)}={_fmt(v)}" for k, v in fields.items()]
    return SEP.join(parts)


def emit(event: str, **fields: Any) -> None:
    """Emit one marker. The turn id comes from the ambient turn context."""
    logger.info(format_marker(event, get_turn_id(), fields))


# ── Parsing ───────────────────────────────────────────────────────────────────

@dataclass
class Marker:
    event: str
    turn: str
    fields: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.fields.get(key, default)

    def flag(self, key: str) -> bool:
        return self.fields.get(key) == "true"

    def number(self, key: str, default: int = 0) -> int:
        try:
            return int(self.fields[key])
        except (KeyError, ValueError):
            return default

    def as_dict(self) -> dict[str, Any]:
        return {"event": self.event, "turn": self.turn, **self.fields}


def parse(line: str) -> Marker | None:
    """Parse a marker line. Returns None for anything that isn't one."""
    line = line.strip()
    if not line.startswith(PREFIX + SEP):
        return None
    parts = line.split(SEP)
    if len(parts) < 2:
        return None
    event = parts[1]
    turn = ""
    fields: dict[str, str] = {}
    for part in parts[2:]:
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key == "turn":
            turn = "" if value == "-" else value
        else:
            fields[key] = value
    return Marker(event=event, turn=turn, fields=fields)
