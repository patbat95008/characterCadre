"""
Phase orchestration for CharacterCadre.

Phase 1   (Director):          structured call → speaker routing + beat transition signal
Phase 1.5 (Director drafting): one free-text call per speaker → isolated scene brief
Phase 2   (Speaker generation): stream DM narration (if requested) then character response
Phase 3   (Option drafting):   structured call → 4 player option strings

Functions mutate the Save object in place (appending to save.messages, updating
save.current_beat_id). The caller (routes/turn.py) is responsible for persisting.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime, timezone
from typing import Optional

from app.models import Beat, Character, DirectorResponse, Message, Save, Scenario
from app.ollama_client import (
    OLLAMA_MODEL,
    OllamaTimeoutError,
    OllamaUnreachableError,
    stream_chat,
    structured_chat,
)
from app.prompt_builder import (
    build_character_prompt,
    build_director_draft_prompt,
    build_director_prompt,
    build_dm_prompt,
)
from app.validation import (
    DIRECTOR_FALLBACK,
    OPTIONS_FALLBACK,
    Ok,
    validate_director_response,
    validate_options_response,
    validate_streamed_text,
    with_validation,
)

logger = logging.getLogger(__name__)

def _build_options_instruction(transition_condition: Optional[str]) -> str:
    base = (
        "Based on the story so far, suggest exactly 4 short player actions or replies "
        "(10 words or fewer each) that the player character might do or say next. "
        "Vary the tone: one cautious, one bold, one curious, one witty. "
        "Return JSON with an 'options' array of exactly 4 objects. "
        "Each object must have:\n"
        "  'text' (the action string, 10 words or fewer)\n"
        "  'advances_beat' (boolean) — true for at most ONE option that would "
        "naturally satisfy the story beat's transition condition; false otherwise\n"
        "  'dice_roll' — either null, or an object {\"dice\": \"D20\"|\"D100\", "
        "\"difficulty\": \"Easy\"|\"Medium\"|\"Hard\"} when the action warrants a "
        "skill check. Use D20 for most checks; D100 for luck or fate-based outcomes. "
        "At most ONE option may have a non-null dice_roll. "
        "dice_roll and advances_beat may coexist on the same option (success advances "
        "the beat, failure does not). Only add dice_roll when the action is genuinely "
        "risky or uncertain — not for simple dialogue or safe actions."
    )
    if transition_condition:
        return (
            base
            + f'\n\nCurrent beat transition condition: "{transition_condition}"\n'
            "If one of the player options would clearly satisfy this condition, "
            "set that option's 'advances_beat' to true. Otherwise leave all false."
        )
    return base


def find_next_beat(save: Save, scenario: Scenario) -> Optional[Beat]:
    """Return the next beat by order after the current one, or None if none exists."""
    if not scenario.beats or not save.current_beat_id or save.sandbox_mode:
        return None
    beats_by_id = {b.id: b for b in scenario.beats}
    current = beats_by_id.get(save.current_beat_id)
    if not current:
        return None
    forward = sorted(
        (b for b in scenario.beats if b.order > current.order),
        key=lambda b: b.order,
    )
    return forward[0] if forward else None


# ── Phase 1: Director ─────────────────────────────────────────────────────────

async def run_director(
    save: Save,
    scenario: Scenario,
    characters: dict[str, Character],
    favored_character_ids: list[str] | None = None,
    response_reserve: int = 1024,
    on_retry: Optional[Callable[[str], Awaitable[None]]] = None,
) -> DirectorResponse:
    """
    Phase 1: Call the Director (structured LLM call) to decide who speaks next
    and whether a beat transition is needed.
    Uses with_validation for auto-retry. Falls back to DIRECTOR_FALLBACK on exhaustion.
    """
    # Determine the first non-DM character as the fallback speaker
    fallback_speaker_id = next(
        (
            cid for cid in save.active_character_ids
            if not characters.get(cid, _dummy_character()).is_dm
        ),
        save.active_character_ids[0] if save.active_character_ids else "",
    )

    schema = DirectorResponse.model_json_schema()

    async def call_director() -> dict:
        messages = build_director_prompt(
            save, scenario, characters,
            favored_character_ids=favored_character_ids,
            response_reserve=response_reserve,
        )
        return await structured_chat(OLLAMA_MODEL, messages, schema)

    async def on_retry_wrapped(reason: str) -> None:
        logger.warning(
            "Director validation failed, retrying (save=%s, reason=%s)", save.id, reason
        )
        if on_retry:
            await on_retry(reason)

    def make_fallback() -> DirectorResponse:
        fallback = DIRECTOR_FALLBACK.model_copy()
        fallback.speaker_character_id = fallback_speaker_id
        return fallback

    logger.info("turn started (save=%s, beat=%s)", save.id, save.current_beat_id or "none")

    return await with_validation(
        call_fn=call_director,
        validator=lambda raw: validate_director_response(raw, scenario, characters, save),
        max_retries=2,
        on_failure=make_fallback,
        on_retry=on_retry_wrapped,
        call_name="director",
    )


def _dummy_character() -> Character:
    return Character(id="", name="", description="", is_dm=False)


# ── Phase 1.5: Director context drafting ─────────────────────────────────────

async def run_director_draft(
    save: Save,
    scenario: Scenario,
    characters: dict[str, Character],
    target_name: str,
    target_role: str,
    direction_note: Optional[str] = None,
    response_reserve: int = 1024,
    num_predict: int | None = None,
) -> str:
    """
    Phase 1.5: Director drafts an isolated scene brief for a single persona.

    Makes a free-text streaming call (not structured JSON). The result is passed
    as context_draft to build_dm_prompt() or build_character_prompt() so that the
    target persona never sees the raw adventure log.
    """
    messages = build_director_draft_prompt(
        save, scenario, characters, target_name, target_role, direction_note,
        response_reserve=response_reserve,
    )
    tokens: list[str] = []
    async for token in stream_chat(OLLAMA_MODEL, messages, num_predict=num_predict):
        tokens.append(token)
    draft = "".join(t for t in tokens if t is not None).strip()
    logger.debug(
        "Director draft complete (save=%s, target=%s, role=%s, chars=%d)",
        save.id,
        target_name,
        target_role,
        len(draft),
    )
    return draft


# ── Ending detection (must run BEFORE apply_beat_transition) ──────────────────

def is_final_beat_completion(
    save: Save,
    scenario: Scenario,
    director_response: DirectorResponse,
) -> bool:
    """
    True if the Director is signaling completion of the LAST beat — i.e. the
    story has ended. Detected when:
      - the scenario has at least one beat
      - the save is currently in the highest-order beat
      - the Director set beat_transition=true (regardless of next_beat_id, which
        may be null or refer to the same beat — there is no further beat to go to)

    The chat-turn endpoint uses this to flip sandbox_mode on and emit
    `event: ending_reached` so the frontend can show the ending modal.
    Must be called BEFORE apply_beat_transition, since that function may mutate
    save.current_beat_id.
    """
    if not scenario.beats or not save.current_beat_id:
        return False
    if not director_response.beat_transition:
        return False
    last_beat = max(scenario.beats, key=lambda b: b.order)
    if save.current_beat_id != last_beat.id:
        return False
    # Director signalled transition while in the last beat → there is nowhere
    # forward to go, so this is the ending.
    next_id = director_response.next_beat_id
    if next_id and next_id != last_beat.id:
        # Director picked a different beat as "next" while in the last — that
        # is invalid (would be backward). apply_beat_transition will reject it.
        # Don't treat as ending; let the validation path log the issue.
        return False
    return True


# ── Beat transition handler ───────────────────────────────────────────────────

def apply_beat_transition(
    save: Save,
    scenario: Scenario,
    director_response: DirectorResponse,
    trigger: str = "director",
) -> Optional[dict]:
    """
    If the Director signaled a valid forward beat transition:
    - Update save.current_beat_id
    - Append the new beat's starter_prompt as a DM message to save.messages
    - Return {"new_beat_id": ..., "new_beat_name": ...} for SSE emit
    Returns None if no transition.
    """
    if not director_response.beat_transition:
        return None
    if not director_response.next_beat_id:
        return None

    beats_by_id = {b.id: b for b in scenario.beats}
    next_beat = beats_by_id.get(director_response.next_beat_id)
    if not next_beat:
        logger.warning(
            "beat transition: next_beat_id '%s' not found in scenario (save=%s)",
            director_response.next_beat_id,
            save.id,
        )
        return None

    old_beat_name = "none"
    if save.current_beat_id:
        old = beats_by_id.get(save.current_beat_id)
        if old:
            old_beat_name = old.name

    save.current_beat_id = next_beat.id

    from app.variables import apply_variables
    starter = apply_variables(next_beat.starter_prompt, save.user_name, char_name=None)
    msg = Message(
        id=str(uuid.uuid4()),
        role="dm",
        character_id=None,
        content=starter,
        timestamp=datetime.now(timezone.utc).isoformat(),
        is_dm_only=False,
        beat_id_at_time=next_beat.id,
    )
    save.messages.append(msg)

    logger.info(
        "beat transition: %s -> %s (save=%s, trigger=%s)",
        old_beat_name,
        next_beat.name,
        save.id,
        trigger,
    )
    return {"new_beat_id": next_beat.id, "new_beat_name": next_beat.name}


# ── Phase 2: Speaker generation ───────────────────────────────────────────────

async def run_phase2(
    save: Save,
    scenario: Scenario,
    characters: dict[str, Character],
    director_response: DirectorResponse,
    response_reserve: int = 1024,
    num_predict: int | None = None,
    on_retry: Optional[Callable[[str], Awaitable[None]]] = None,
) -> AsyncGenerator[dict, None]:
    """
    Phase 2: Async generator that streams DM narration (if dm_should_narrate) then
    the chosen character's response. Yields event dicts:
      {"event": "token", "character_id": ..., "text": ...}
      {"event": "message_complete", "message_id": ..., "character_id": ...}
      {"event": "regenerate", "reason": ...}
      {"event": "validation_warning", "reason": ...}
    Tokens are emitted optimistically; frontend treats them as provisional until
    message_complete fires.
    """
    return _phase2_gen(save, scenario, characters, director_response, response_reserve, num_predict, on_retry)


async def _phase2_gen(
    save: Save,
    scenario: Scenario,
    characters: dict[str, Character],
    director_response: DirectorResponse,
    response_reserve: int = 1024,
    num_predict: int | None = None,
    on_retry: Optional[Callable[[str], Awaitable[None]]] = None,
) -> AsyncGenerator[dict, None]:
    dm_char = next(
        (c for c in characters.values() if c.is_dm and c.id in save.active_character_ids),
        None,
    )
    companion_names = [
        c.name for c in characters.values()
        if not c.is_dm and c.id in save.active_character_ids
    ]

    def _last_generated_content_for(char_id: str) -> Optional[str]:
        """Return the most recent generated response from char_id.
        Only looks at messages after the first user message to avoid comparing
        against the scenario's seed/opening message."""
        first_user_idx = next(
            (i for i, m in enumerate(save.messages) if m.role == "user"),
            None,
        )
        if first_user_idx is None:
            return None
        for msg in reversed(save.messages[first_user_idx:]):
            if msg.character_id == char_id and msg.role in ("dm", "character"):
                return msg.content
        return None

    # ── DM narration (if requested) ───────────────────────────────────────────
    if director_response.dm_should_narrate and dm_char:
        dm_context = await run_director_draft(
            save, scenario, characters,
            target_name=dm_char.name,
            target_role="narrator",
            direction_note=director_response.direction_note or None,
            response_reserve=response_reserve,
            num_predict=num_predict,
        )
        dm_messages = build_dm_prompt(
            save, scenario, dm_char,
            context_draft=dm_context,
            companion_names=companion_names,
        )
        async for event in _stream_speaker(
            messages=dm_messages,
            character_id=dm_char.id,
            role="dm",
            save=save,
            previous_content=_last_generated_content_for(dm_char.id),
            same_speaker_as_previous=True,
            on_retry=on_retry,
            num_predict=num_predict,
        ):
            yield event

    # ── Chosen character response ─────────────────────────────────────────────
    speaker_id = director_response.speaker_character_id

    # Director may set speaker_character_id=None to mean "no follow-up speaker —
    # player goes next." If narration also didn't happen, the turn produces no
    # content; emit a UI notice so the player knows, and let the route proceed
    # to Phase 3 (options) so they can recover by acting next themselves.
    if speaker_id is None:
        if not director_response.dm_should_narrate:
            logger.warning(
                "Phase 2: director chose null speaker AND no DM narration "
                "(save=%s) — turn produced no content",
                save.id,
            )
            yield {
                "event": "notice",
                "level": "warning",
                "message": "Warning: LLM chose to do nothing",
            }
        else:
            logger.info(
                "Phase 2: director chose null speaker after DM narration (save=%s) "
                "— companions will not speak this turn",
                save.id,
            )
        return

    speaker = characters.get(speaker_id)
    if speaker is None:
        logger.warning(
            "Phase 2: speaker '%s' not found in characters dict (save=%s)",
            speaker_id,
            save.id,
        )
        return

    if speaker.is_dm and dm_char:
        # DM is chosen speaker; draft a narrator brief (only if we didn't already narrate)
        speaker_context = await run_director_draft(
            save, scenario, characters,
            target_name=speaker.name,
            target_role="narrator",
            direction_note=director_response.direction_note or None,
            response_reserve=response_reserve,
            num_predict=num_predict,
        )
        speaker_messages = build_dm_prompt(
            save, scenario, speaker,
            context_draft=speaker_context,
            companion_names=companion_names,
        )
    else:
        char_context = await run_director_draft(
            save, scenario, characters,
            target_name=speaker.name,
            target_role="companion",
            direction_note=director_response.direction_note or None,
            response_reserve=response_reserve,
            num_predict=num_predict,
        )
        speaker_messages = build_character_prompt(
            speaker, scenario, save, save.user_name,
            context_draft=char_context,
        )

    async for event in _stream_speaker(
        messages=speaker_messages,
        character_id=speaker_id,
        role="dm" if speaker.is_dm else "character",
        save=save,
        previous_content=_last_generated_content_for(speaker_id),
        same_speaker_as_previous=True,
        on_retry=on_retry,
        num_predict=num_predict,
    ):
        yield event


_PREVIEW_WORDS = 25


def _preview_buffered(buffered: str | None) -> str:
    """Return a compact, log-safe preview of a streamed buffer for diagnostics."""
    if buffered is None:
        return "<null>"
    if buffered == "":
        return "<empty>"
    stripped = buffered.strip()
    if not stripped:
        return f"<whitespace-only len={len(buffered)} repr={buffered!r}>"
    words = stripped.split()
    head = " ".join(words[:_PREVIEW_WORDS])
    suffix = "…" if len(words) > _PREVIEW_WORDS else ""
    # Collapse newlines/tabs so the log line stays single-line.
    head = " ".join(head.split())
    return f"({len(words)}w) {head}{suffix}"


async def _stream_speaker(
    messages: list[dict[str, str]],
    character_id: str,
    role: str,
    save: Save,
    previous_content: Optional[str],
    same_speaker_as_previous: bool,
    on_retry: Optional[Callable[[str], Awaitable[None]]] = None,
    max_retries: int = 2,
    num_predict: int | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Stream a single speaker's response.

    - Loop detected: commit the response immediately, emit validation_warning so the
      player can see the message and decide whether to regenerate it themselves.
    - Empty response: auto-retry (invisible to the player) up to max_retries times.
    """
    for attempt in range(max_retries + 1):
        buffer: list[str] = []
        try:
            async for token in stream_chat(OLLAMA_MODEL, messages, num_predict=num_predict):
                buffer.append(token)
                yield {"event": "token", "character_id": character_id, "text": token}
        except (OllamaTimeoutError, OllamaUnreachableError):
            raise  # propagate to route handler

        buffered = "".join(t for t in buffer if t is not None)
        result = validate_streamed_text(buffered, previous_content, same_speaker_as_previous)

        if isinstance(result, Ok):
            msg_id = _commit_message(save, role, character_id, buffered)
            yield {"event": "message_complete", "message_id": msg_id, "character_id": character_id}
            return

        # Loop detected: commit and warn — the player decides whether to regenerate
        if result.reason.startswith("loop detected"):
            logger.warning(
                "loop detected (save=%s, character_id=%s), committing with warning",
                save.id,
                character_id,
            )
            msg_id = _commit_message(save, role, character_id, buffered)
            yield {"event": "validation_warning", "reason": result.reason}
            yield {"event": "message_complete", "message_id": msg_id, "character_id": character_id}
            return

        # Empty or other error: auto-retry
        preview = _preview_buffered(buffered)
        if attempt < max_retries:
            logger.warning(
                "empty response (save=%s, character_id=%s), regenerating (attempt=%d/%d) | preview=%s",
                save.id,
                character_id,
                attempt + 1,
                max_retries + 1,
                preview,
            )
            if on_retry:
                await on_retry(result.reason)
            yield {"event": "regenerate", "reason": result.reason, "character_id": character_id}
        else:
            logger.warning(
                "streaming validation exhausted (save=%s, character_id=%s), emitting with warning | preview=%s",
                save.id,
                character_id,
                preview,
            )
            msg_id = _commit_message(save, role, character_id, buffered)
            yield {"event": "validation_warning", "reason": result.reason}
            yield {"event": "message_complete", "message_id": msg_id, "character_id": character_id}
            return


def _commit_message(save: Save, role: str, character_id: str, content: str) -> str:
    """Append a completed message to save.messages and return its ID."""
    msg_id = str(uuid.uuid4())
    msg = Message(
        id=msg_id,
        role=role,  # type: ignore[arg-type]
        character_id=character_id,
        content=content,
        timestamp=datetime.now(timezone.utc).isoformat(),
        is_dm_only=False,
        beat_id_at_time=save.current_beat_id,
    )
    save.messages.append(msg)
    preview = content.replace("\n", " ")[:60]
    logger.info(
        "response committed (save=%s, role=%s, char=%s): %s",
        save.id, role, character_id, preview,
    )
    return msg_id


# ── Phase 3: Option drafting ──────────────────────────────────────────────────

async def run_phase3(
    save: Save,
    scenario: Scenario,
    characters: dict[str, Character],
    direction_note: Optional[str] = None,
    response_reserve: int = 1024,
    num_predict: int | None = None,
    on_retry: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[list[dict], str]:
    """
    Phase 3: Generate 2-6 plausible player options using DM Prompt Assembly.
    The Director first drafts an options context brief; the DM then uses only
    its character card plus that brief (no raw adventure log).
    Falls back to OPTIONS_FALLBACK on exhaustion.

    Returns (options, context_draft) where each option is
    {"text": str, "advances_beat": bool, "dice_roll": {"dice":..,"difficulty":..}|None}.
    At most one may have advances_beat=True; at most one may have a non-null dice_roll.
    """
    dm_char = next(
        (c for c in characters.values() if c.is_dm and c.id in save.active_character_ids),
        None,
    )
    if dm_char is None:
        logger.warning("Phase 3: no DM character found in active roster (save=%s)", save.id)
        return list(OPTIONS_FALLBACK), ""

    # Determine if a beat advance is possible so the LLM can flag an option
    next_beat = find_next_beat(save, scenario)
    transition_condition = next_beat.transition_condition if next_beat else None
    options_instruction = _build_options_instruction(transition_condition)

    _dice_roll_schema = {
        "oneOf": [
            {"type": "null"},
            {
                "type": "object",
                "properties": {
                    "dice": {"type": "string", "enum": ["D20", "D100"]},
                    "difficulty": {"type": "string", "enum": ["Easy", "Medium", "Hard"]},
                },
                "required": ["dice", "difficulty"],
            },
        ]
    }

    options_schema = {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "minItems": 2,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "advances_beat": {"type": "boolean"},
                        "dice_roll": _dice_roll_schema,
                    },
                    "required": ["text", "advances_beat", "dice_roll"],
                },
            }
        },
        "required": ["options"],
    }

    companion_names = [
        c.name for c in characters.values()
        if not c.is_dm and c.id in save.active_character_ids
    ]

    # Phase 1.5 for options: director drafts a situation brief
    options_context = await run_director_draft(
        save, scenario, characters,
        target_name=dm_char.name,
        target_role="options",
        direction_note=direction_note,
        response_reserve=response_reserve,
        num_predict=num_predict,
    )

    async def call_options() -> dict:
        base_messages = build_dm_prompt(
            save, scenario, dm_char,
            context_draft=options_context,
            companion_names=companion_names,
        )
        messages = base_messages + [{"role": "system", "content": options_instruction}]
        return await structured_chat(OLLAMA_MODEL, messages, options_schema)

    async def on_retry_wrapped(reason: str) -> None:
        logger.warning(
            "Options validation failed, retrying (save=%s, reason=%s)", save.id, reason
        )
        if on_retry:
            await on_retry(reason)

    options = await with_validation(
        call_fn=call_options,
        validator=validate_options_response,
        max_retries=2,
        on_failure=lambda: list(OPTIONS_FALLBACK),
        on_retry=on_retry_wrapped,
        call_name="options",
    )
    return options, options_context
