"""
Soft probes — cheap keyword signals over the LLM's prose.

These never fail a run. They exist so the report can say "the DM conjured a
barkeep on step 3" without a human re-reading 4,000 words of transcript to find
it. Every hit points at a line in transcript.md; the transcript is the evidence,
the probe is just the index.

Keyword matching is crude on purpose. A false positive costs a glance at the
transcript; building an LLM judge to avoid that would double the runtime and
need tuning of its own.
"""
from __future__ import annotations

import re
from typing import Optional

from tests.playtest.model import Probe, TurnResult

# Nothing in the Ironroot fixtures defines a settlement, an inn, or any NPC the
# party could buy a drink from — the scenario opens at the dungeon archway and
# the only world note says the complex was abandoned three centuries ago. Any of
# these words means the DM improvised a location out of whole cloth, which is
# the intended behaviour when a player leaves the rails. The probe records where
# it happened so the improvisation can be reviewed for continuity, not so it can
# be stamped out.
_IMPROVISED_LOCATION = re.compile(
    r"\b(tavern|inn(?:keeper)?|barkeep|barmaid|alehouse|publican|"
    r"village|hamlet|town square|marketplace|hearth ?fire)\b",
    re.IGNORECASE,
)

# The Lich King's vulnerability to mundane steel is flagged DO NOT REVEAL OR
# HINT. This looks for the secret being stated rather than demonstrated.
_WEAPON = r"(?:steel|sword|blade|axe|mundane|ordinary|normal|physical)"
_VULNERABLE = r"(?:vulnerab\w*|weak\w*|not immune|no defence|no defense|can be (?:hurt|killed|wounded))"
_WEAKNESS_LEAK = re.compile(
    rf"{_WEAPON}[^.!?]{{0,80}}{_VULNERABLE}|{_VULNERABLE}[^.!?]{{0,80}}{_WEAPON}",
    re.IGNORECASE,
)

_MENU_BOT = re.compile(r"\[\s*[12]\s*\]")

_OOC_ACKNOWLEDGED = re.compile(
    r"\b(out of character|OOC|as an AI|I'm an AI|language model|"
    r"as the (?:narrator|DM|dungeon master),? I)\b",
    re.IGNORECASE,
)


def _hit(name: str, detail: str) -> str:
    return f"{name}: {detail}"


def _quote(text: str, match: re.Match) -> str:
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 40)
    return "…" + " ".join(text[start:end].split()) + "…"


def improvised_location() -> Probe:
    """Where did the DM build scenery the world data doesn't contain?

    Spinning a yarn when the player walks off the rails is the point of the app,
    so a hit here is a feature working. What it gives you is a cursor into the
    transcript: which turn invented what, so improvised detail can be checked
    for continuity across turns and against the established world.
    """
    def probe(t: TurnResult) -> Optional[str]:
        match = _IMPROVISED_LOCATION.search(t.prose)
        if not match:
            return None
        return _hit("improvised_location", f"{match.group(0)!r} — {_quote(t.prose, match)}")
    return probe


def weakness_leak() -> Probe:
    """Did anyone state the Lich King's secret weakness outright?"""
    def probe(t: TurnResult) -> Optional[str]:
        match = _WEAKNESS_LEAK.search(t.prose)
        if not match:
            return None
        return _hit("weakness_leak", _quote(t.prose, match))
    return probe


def menu_bot_format() -> Probe:
    """The final beat asks the Narrator to drop character and read a [1]/[2]
    menu. Records whether the bracketed format actually appeared."""
    def probe(t: TurnResult) -> Optional[str]:
        if _MENU_BOT.search(t.prose):
            return _hit("menu_bot_format", "bracketed menu options present")
        return _hit("menu_bot_format", "MISSING — no [1]/[2] menu in the narration")
    return probe


def broke_character() -> Probe:
    """Did a persona acknowledge the fourth wall or admit to being an AI?"""
    def probe(t: TurnResult) -> Optional[str]:
        match = _OOC_ACKNOWLEDGED.search(t.prose)
        if not match:
            return None
        return _hit("broke_character", _quote(t.prose, match))
    return probe


def companion_silent(character_id: str, name: str) -> Probe:
    """Records when a named companion never got a turn."""
    def probe(t: TurnResult) -> Optional[str]:
        if t.spoke(character_id):
            return None
        return _hit("companion_silent", f"{name} did not speak")
    return probe


def name_prefix_leak() -> Probe:
    """Did a persona label its own line, screenplay-style?

    Each character speaks in its own message with its own character_id, so the
    UI already attributes it. A "Bram Stonefist:" prefix in the body is the
    model bleeding the roster listing into its output.
    """
    def probe(t: TurnResult) -> Optional[str]:
        offenders = []
        for msg in t.messages:
            if msg.character_id is None:
                continue
            head = msg.content.lstrip()[: len(msg.character_name) + 2]
            if head.lower().startswith(msg.character_name.lower() + ":"):
                offenders.append(msg.character_name)
        if not offenders:
            return None
        return _hit("name_prefix_leak", f"{', '.join(offenders)} prefixed their own line")
    return probe


def ooc_honored() -> Probe:
    """For the direct 'advance the scene please' step: did it actually move?"""
    def probe(t: TurnResult) -> Optional[str]:
        if t.beat_advanced:
            return _hit("ooc_honored", f"free-text OOC advanced the beat to {t.beat_after}")
        return _hit("ooc_honored", "NOT honored — free-text OOC did not advance the beat")
    return probe
