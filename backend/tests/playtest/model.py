"""
Data shapes shared by the harness, the checks, the probes and the report writer.

Kept separate from harness.py so checks.py and probes.py can type against them
without importing the runner.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from app.markers import Marker

Status = Literal["PASS", "FAIL", "WARN", "SKIP"]


# ── Results ───────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    step: int = -1
    label: str = ""

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"

    def __str__(self) -> str:
        where = f"step {self.step}" + (f" ({self.label})" if self.label else "")
        return f"[{self.status}] {where} · {self.name}: {self.detail}".rstrip(": ")


@dataclass
class SpokenMessage:
    """One committed message, as reconstructed from the save after the turn."""
    message_id: str
    role: str
    character_id: Optional[str]
    character_name: str
    content: str


@dataclass
class TurnResult:
    """Everything observable about one turn."""
    index: int
    label: str
    user_message: str
    request: dict[str, Any]
    events: list[tuple[str, Any]] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    duration_s: float = 0.0

    beat_before: Optional[str] = None
    beat_after: Optional[str] = None
    sandbox_after: bool = False

    # Reconstructed from the streamed tokens — i.e. what a player would have seen.
    streamed: dict[str, str] = field(default_factory=dict)
    # Authoritative: diffed out of the save file after the turn.
    messages: list[SpokenMessage] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    options_context: str = ""

    probe_hits: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    skipped_reason: str = ""

    # Set only on option-driven runs: the options this turn chose *from* (i.e.
    # the previous turn's `options`) and which one was taken.
    offered_options: list[dict] = field(default_factory=list)
    chosen_index: Optional[int] = None
    chosen_option: Optional[dict] = None

    # ── Event accessors ───────────────────────────────────────────────────────

    def events_named(self, name: str) -> list[Any]:
        return [payload for evt, payload in self.events if evt == name]

    def first(self, name: str) -> Optional[Any]:
        found = self.events_named(name)
        return found[0] if found else None

    def markers_named(self, event: str) -> list[Marker]:
        return [m for m in self.markers if m.event == event]

    @property
    def error(self) -> Optional[str]:
        payload = self.first("error")
        return payload.get("reason") if isinstance(payload, dict) else None

    @property
    def director(self) -> dict:
        payload = self.first("director")
        return payload if isinstance(payload, dict) else {}

    @property
    def roll(self) -> Optional[dict]:
        payload = self.first("roll_result")
        return payload if isinstance(payload, dict) else None

    @property
    def beat_advanced(self) -> bool:
        return bool(self.markers_named("beat.advance"))

    @property
    def ending_reached(self) -> bool:
        return bool(self.events_named("ending_reached"))

    @property
    def used_fallback(self) -> bool:
        return bool(self.markers_named("validation.fallback"))

    @property
    def prose(self) -> str:
        """All committed prose this turn, for keyword probing."""
        return "\n".join(m.content for m in self.messages)

    def spoke(self, character_id: str) -> bool:
        return any(m.character_id == character_id for m in self.messages)


# ── Script DSL ────────────────────────────────────────────────────────────────

Check = Callable[[TurnResult], CheckResult]
Probe = Callable[[TurnResult], Optional[str]]


@dataclass
class Step:
    """One scripted player action.

    Either a turn (`say`) or a direct beat jump (`jump_to`), which uses the
    manual advance-beat endpoint to skip ahead without spending LLM time.
    """
    say: str = ""
    label: str = ""
    beat_advance: bool = False
    use_offered_dice: bool = False
    dice_roll: Optional[dict] = None
    jump_to: Optional[str] = None
    # Re-run this step when an attached roll fails, so a scripted "you get past
    # the trap" beat isn't hostage to an unseeded d20.
    retry_on_dice_failure: int = 0
    expect: list[Check] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)


@dataclass
class PlaytestScript:
    name: str
    description: str
    steps: list[Step] = field(default_factory=list)


# ── Option-driven runs ────────────────────────────────────────────────────────

@dataclass
class OptionPolicy:
    """How to choose among the options the LLM offered.

    Scripted runs test "can the player drive the story". These test the opposite:
    can the *model's own suggestions* drive it, with no authored intent at all.

    `pick` receives the offered options and a seeded RNG, and returns an index.
    """
    name: str
    description: str
    pick: Callable[[list[dict], random.Random], int]
