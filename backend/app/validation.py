from __future__ import annotations

import difflib
import logging
import re
import string
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.models import Character, DirectorResponse, Save, Scenario

logger = logging.getLogger(__name__)

T = TypeVar("T")
V = TypeVar("V")


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class Ok(Generic[T]):
    value: T


@dataclass
class Err:
    reason: str


Result = Ok | Err


# ── Fallback constants ────────────────────────────────────────────────────────

DIRECTOR_FALLBACK = DirectorResponse(
    speaker_character_id="",  # filled at call site in phases.py
    dm_should_narrate=True,
    beat_transition=False,
    next_beat_id=None,
    direction_note="",
    reasoning="",
)

OPTIONS_FALLBACK: list[str] = [
    "Wait and observe.",
    "Ask a question.",
    "Take action.",
    "Step back.",
]


# ── Validators ────────────────────────────────────────────────────────────────

def validate_director_response(
    raw: dict,
    scenario: Scenario,
    characters: dict[str, Character],
    save: Save,
) -> Result[DirectorResponse]:
    """
    Parse raw dict into DirectorResponse and enforce business rules.
    """
    try:
        resp = DirectorResponse.model_validate(raw)
    except Exception as exc:
        return Err(f"schema validation failed: {exc}")

    # speaker_character_id may be None to signal "no follow-up speaker — player goes next".
    # If non-null, it must reference a real, active character.
    if resp.speaker_character_id is not None:
        if resp.speaker_character_id not in save.active_character_ids:
            return Err(
                f"speaker_character_id '{resp.speaker_character_id}' not in active roster"
            )
        if resp.speaker_character_id not in characters:
            return Err(
                f"speaker_character_id '{resp.speaker_character_id}' not found in characters"
            )

    # beat transition rules
    if resp.beat_transition:
        if save.sandbox_mode:
            return Err("beat_transition=True but save is in sandbox_mode")
        if not scenario.beats:
            return Err("beat_transition=True but scenario has no beats")
        if not resp.next_beat_id:
            return Err("beat_transition=True but next_beat_id is missing")

        # validate next_beat_id is a forward beat
        beats_by_id = {b.id: b for b in scenario.beats}
        if resp.next_beat_id not in beats_by_id:
            return Err(f"next_beat_id '{resp.next_beat_id}' not found in scenario beats")

        next_beat = beats_by_id[resp.next_beat_id]
        if save.current_beat_id:
            current_beat = beats_by_id.get(save.current_beat_id)
            if current_beat and next_beat.order <= current_beat.order:
                return Err(
                    f"next_beat_id '{resp.next_beat_id}' (order={next_beat.order}) is not "
                    f"forward from current beat (order={current_beat.order})"
                )

    return Ok(resp)


OPTIONS_MIN = 2
OPTIONS_MAX = 6


def validate_options_response(raw: dict) -> Result[list[str]]:
    """
    Expects {"options": [...]} with OPTIONS_MIN..OPTIONS_MAX non-empty strings.
    """
    if not isinstance(raw, dict):
        return Err("response is not a dict")
    options = raw.get("options")
    if options is None:
        return Err("missing 'options' key")
    if not isinstance(options, list):
        return Err("'options' is not a list")
    if not (OPTIONS_MIN <= len(options) <= OPTIONS_MAX):
        return Err(
            f"expected {OPTIONS_MIN}-{OPTIONS_MAX} options, got {len(options)}"
        )
    for i, opt in enumerate(options):
        if not isinstance(opt, str):
            return Err(f"option[{i}] is not a string")
        if not opt.strip():
            return Err(f"option[{i}] is empty")
    return Ok(options)


def validate_streamed_text(
    buffered: str,
    previous_message: str | None,
    same_speaker: bool,
) -> Result[str]:
    """
    Validates a buffered streaming response.
    Fails if empty or if it forms a loop with the previous message.
    """
    if not buffered.strip():
        return Err("response is empty or whitespace-only")

    if previous_message is not None:
        loop, ratio = is_loop(buffered, previous_message, same_speaker)
        if loop:
            return Err(f"loop detected (similarity={ratio:.2f}, same_speaker={same_speaker})")

    return Ok(buffered)


# ── Loop detection ────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


LOOP_MIN_LENGTH = 40


def is_loop(
    new_text: str,
    previous_text: str,
    same_speaker: bool,
) -> tuple[bool, float]:
    """
    Returns (is_looping, similarity_ratio).
    Skips check when normalized new_text is shorter than LOOP_MIN_LENGTH —
    short replies are inherently high-collision and almost never real loops.
    Threshold: 0.92 if same_speaker, 0.97 if different speaker.
    """
    a = _normalize(new_text)
    b = _normalize(previous_text)
    if len(a) < LOOP_MIN_LENGTH:
        return False, 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    threshold = 0.92 if same_speaker else 0.97
    return ratio >= threshold, ratio


# ── Retry orchestrator ────────────────────────────────────────────────────────

async def with_validation(
    call_fn: Callable[[], Awaitable[T]],
    validator: Callable[[T], Result[V]],
    max_retries: int = 2,
    on_failure: Callable[[], V] | None = None,
    on_retry: Callable[[str], Awaitable[None]] | None = None,
    call_name: str = "unknown",
) -> V:
    """
    Calls call_fn(), validates the result, retries on failure.
    After max_retries exhausted, calls on_failure() if provided, else raises.
    on_retry(reason) is awaited before each retry (for SSE event emit).
    """
    last_reason = ""
    for attempt in range(max_retries + 1):
        raw = await call_fn()
        result = validator(raw)
        if isinstance(result, Ok):
            return result.value

        last_reason = result.reason
        logger.warning(
            "validation failed (call=%s, reason=%s, attempt=%d/%d)",
            call_name,
            last_reason,
            attempt + 1,
            max_retries + 1,
        )

        if attempt < max_retries:
            if on_retry is not None:
                await on_retry(last_reason)
        else:
            logger.warning(
                "fallback engaged (call=%s, reason=%s)", call_name, last_reason
            )
            if on_failure is not None:
                return on_failure()
            raise RuntimeError(
                f"Validation failed after {max_retries + 1} attempts for '{call_name}': {last_reason}"
            )

    # unreachable but satisfies type checker
    raise RuntimeError("with_validation: exhausted retries without returning")
