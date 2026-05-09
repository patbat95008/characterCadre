"""
Background summarization for characters, scenarios, and beats.

The Director needs short summaries to route between speakers and reason about
plot beats without reading every character's full description on every turn.
This module generates those summaries via Ollama and tracks freshness through
SHA-256 hashes of the underlying source fields.

Public API:
    - generate_character_summary / generate_scenario_summary / generate_beat_summary
    - character_description_hash / scenario_summary_hash / beat_summary_hash
    - regen_character_if_stale / regen_scenario_if_stale / regen_beat_if_stale
      (these are scheduled by route handlers via FastAPI BackgroundTasks)

On Ollama failure or invalid response, the summary is left as the empty string.
The prompt builder gracefully falls back to a description excerpt when the
summary is empty, so this never breaks gameplay.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from app import storage
from app.models import Beat, Character, Scenario
from app.ollama_client import (
    OLLAMA_MODEL,
    OllamaTimeoutError,
    OllamaUnreachableError,
    structured_chat,
)

logger = logging.getLogger(__name__)

# A single-key schema keeps Ollama's structured output well-behaved without
# constraining the model to JSON we then have to strip.
_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


# ── Hashing ───────────────────────────────────────────────────────────────────

def _sha256_short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def character_description_hash(description: str) -> str:
    return _sha256_short(description)


def scenario_summary_hash(initial_message: str, system_prompt: str) -> str:
    return _sha256_short(initial_message + "\x1f" + system_prompt)


def beat_summary_hash(name: str, description: str, transition_condition: str) -> str:
    return _sha256_short(name + "\x1f" + description + "\x1f" + transition_condition)


# ── Prompts ───────────────────────────────────────────────────────────────────

_CHARACTER_PROMPT = (
    "Summarize the following character into ONE short sentence (max 20 words) "
    "capturing their core personality and role. This summary will help a "
    "director AI decide when this character should speak. Do not repeat the "
    "name. Do not add quotes.\n\n"
    "Name: {name}\n"
    "Description: {description}"
)

_SCENARIO_PROMPT = (
    "Summarize the following scenario into 1-2 short sentences (max 50 words) "
    "describing the setting, situation, and tone. This summary will orient a "
    "director AI that routes dialogue between characters. Do not add quotes.\n\n"
    "Name: {name}\n"
    "Initial message: {initial_message}"
)

_BEAT_PROMPT = (
    "Summarize the following story beat into ONE short sentence (max 25 words) "
    "describing what happens in this scene and what the player is doing. This "
    "summary will help a director AI decide when to advance the plot or skip "
    "ahead. Do not add quotes.\n\n"
    "Name: {name}\n"
    "Description: {description}\n"
    "Transition: {transition_condition}"
)


async def _call_summary(prompt: str) -> str:
    messages = [
        {"role": "system", "content": "You write extremely concise summaries."},
        {"role": "user", "content": prompt},
    ]
    try:
        result = await structured_chat(OLLAMA_MODEL, messages, _SUMMARY_SCHEMA)
    except (OllamaTimeoutError, OllamaUnreachableError) as exc:
        logger.warning("Summarizer Ollama call failed: %s", exc)
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("Summarizer call raised unexpectedly: %s", exc)
        return ""

    summary = (result or {}).get("summary", "")
    if not isinstance(summary, str):
        return ""
    return summary.strip().strip('"').strip()


async def generate_character_summary(name: str, description: str) -> str:
    return await _call_summary(_CHARACTER_PROMPT.format(name=name, description=description))


async def generate_scenario_summary(
    name: str, initial_message: str, system_prompt: str  # noqa: ARG001
) -> str:
    return await _call_summary(
        _SCENARIO_PROMPT.format(name=name, initial_message=initial_message)
    )


async def generate_beat_summary(
    name: str, description: str, transition_condition: str
) -> str:
    return await _call_summary(
        _BEAT_PROMPT.format(
            name=name,
            description=description,
            transition_condition=transition_condition,
        )
    )


# ── Background tasks (called via FastAPI BackgroundTasks) ─────────────────────

async def regen_character_if_stale(character_id: str) -> None:
    """
    Re-read the character from disk, regenerate the summary if `description_hash`
    no longer matches the current description, persist the result.
    Reads fresh from disk to avoid clobbering concurrent edits.
    """
    character = storage.get_character(character_id)
    if character is None:
        logger.warning("regen_character_if_stale: character %s not found", character_id)
        return
    expected_hash = character_description_hash(character.description)
    if character.description_hash == expected_hash and character.description_summary:
        logger.debug(
            "regen_character_if_stale: skipping %s (hash up to date)", character_id
        )
        return
    summary = await generate_character_summary(character.name, character.description)
    if not summary:
        return
    # Re-read in case the user edited again while we were waiting on Ollama
    fresh = storage.get_character(character_id)
    if fresh is None:
        return
    if character_description_hash(fresh.description) != expected_hash:
        logger.info(
            "regen_character_if_stale: description changed mid-flight for %s, dropping summary",
            character_id,
        )
        return
    fresh.description_summary = summary
    fresh.description_hash = expected_hash
    storage.save_character(fresh)
    logger.info("summary regenerated (kind=character, id=%s)", character_id)


async def regen_scenario_if_stale(scenario_id: str) -> None:
    scenario = storage.get_scenario(scenario_id)
    if scenario is None:
        logger.warning("regen_scenario_if_stale: scenario %s not found", scenario_id)
        return
    expected_hash = scenario_summary_hash(scenario.initial_message, scenario.system_prompt)
    if scenario.summary_hash == expected_hash and scenario.summary:
        return
    summary = await generate_scenario_summary(
        scenario.name, scenario.initial_message, scenario.system_prompt
    )
    if not summary:
        return
    fresh = storage.get_scenario(scenario_id)
    if fresh is None:
        return
    if scenario_summary_hash(fresh.initial_message, fresh.system_prompt) != expected_hash:
        return
    fresh.summary = summary
    fresh.summary_hash = expected_hash
    storage.save_scenario(fresh)
    logger.info("summary regenerated (kind=scenario, id=%s)", scenario_id)


async def regen_beat_if_stale(scenario_id: str, beat_id: str) -> None:
    scenario = storage.get_scenario(scenario_id)
    if scenario is None:
        return
    beat = next((b for b in scenario.beats if b.id == beat_id), None)
    if beat is None:
        logger.warning(
            "regen_beat_if_stale: beat %s not found in scenario %s", beat_id, scenario_id
        )
        return
    expected_hash = beat_summary_hash(beat.name, beat.description, beat.transition_condition)
    if beat.summary_hash == expected_hash and beat.summary:
        return
    summary = await generate_beat_summary(
        beat.name, beat.description, beat.transition_condition
    )
    if not summary:
        return
    fresh = storage.get_scenario(scenario_id)
    if fresh is None:
        return
    fresh_beat = next((b for b in fresh.beats if b.id == beat_id), None)
    if fresh_beat is None:
        return
    if beat_summary_hash(
        fresh_beat.name, fresh_beat.description, fresh_beat.transition_condition
    ) != expected_hash:
        return
    fresh_beat.summary = summary
    fresh_beat.summary_hash = expected_hash
    storage.save_scenario(fresh)
    logger.info(
        "summary regenerated (kind=beat, scenario_id=%s, beat_id=%s)",
        scenario_id,
        beat_id,
    )


# ── Synchronous variants (for explicit "Regenerate" button) ───────────────────

async def regenerate_character_summary_sync(character_id: str) -> Optional[str]:
    """Synchronous regen used by the "↻ Regenerate" button. Returns the new summary."""
    character = storage.get_character(character_id)
    if character is None:
        return None
    summary = await generate_character_summary(character.name, character.description)
    character.description_summary = summary
    character.description_hash = character_description_hash(character.description)
    storage.save_character(character)
    return summary


async def regenerate_scenario_summary_sync(scenario_id: str) -> Optional[str]:
    scenario = storage.get_scenario(scenario_id)
    if scenario is None:
        return None
    summary = await generate_scenario_summary(
        scenario.name, scenario.initial_message, scenario.system_prompt
    )
    scenario.summary = summary
    scenario.summary_hash = scenario_summary_hash(
        scenario.initial_message, scenario.system_prompt
    )
    storage.save_scenario(scenario)
    return summary


async def regenerate_beat_summary_sync(scenario_id: str, beat_id: str) -> Optional[str]:
    scenario = storage.get_scenario(scenario_id)
    if scenario is None:
        return None
    beat = next((b for b in scenario.beats if b.id == beat_id), None)
    if beat is None:
        return None
    summary = await generate_beat_summary(
        beat.name, beat.description, beat.transition_condition
    )
    beat.summary = summary
    beat.summary_hash = beat_summary_hash(
        beat.name, beat.description, beat.transition_condition
    )
    storage.save_scenario(scenario)
    return summary


# ── Hash-stamping helpers used during create/update ───────────────────────────

def stamp_character_hash(character: Character) -> Character:
    """Set description_hash to match description. Does NOT touch the summary."""
    character.description_hash = character_description_hash(character.description)
    return character


def stamp_scenario_hash(scenario: Scenario) -> Scenario:
    scenario.summary_hash = scenario_summary_hash(
        scenario.initial_message, scenario.system_prompt
    )
    for beat in scenario.beats:
        beat.summary_hash = beat_summary_hash(
            beat.name, beat.description, beat.transition_condition
        )
    return scenario


def stamp_beat_hash(beat: Beat) -> Beat:
    beat.summary_hash = beat_summary_hash(
        beat.name, beat.description, beat.transition_condition
    )
    return beat
