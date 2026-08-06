"""
Prompt builder for Character, Director, Director-Draft, and DM Prompt Assemblies.

Token counting: tiktoken with cl100k_base encoding (GPT-4 tokenizer).
Used as an approximation for Llama/Mistral-family models — actual token counts
will differ, but the approximation is close enough for budget management.
The 1024-token response reserve provides additional safety margin.

Character Prompt Assembly order:
  1. system_prompt      (scenario)
  2. description        (character)
  3. response_examples  (character, if any)
  4. persistent_messages (scenario, if any)
  5. context_draft      (director-authored scene brief, as "user" message)

Director Prompt Assembly order:
  1. Hardcoded neutral director system prompt
  2. Scenario name + summary (fallback: first 200 chars of initial_message)
  3. Character roster (name, role, description_summary fallback to description[:150])
  4. Beat roster (only if beats exist and not sandbox_mode)
  5. persistent_messages + dm_only_info
  6. Truncated shared chat (DM-only messages visible to Director)
  7. Hardcoded director instruction

Director Draft Prompt Assembly order (Phase 1.5 — one call per speaker):
  1. Hardcoded neutral director system prompt
  2. Scenario name + summary
  3. Character roster
  4. Truncated shared chat (Director sees all messages)
  5. Drafting instruction for the specific target persona

DM Prompt Assembly order:
  1. system_prompt (scenario)
  2. DM character description + response_examples
  3. Companion exclusion note (if companion_names provided)
  4. persistent_messages + dm_only_info
  5. Active beat context (if current_beat_id is set)
  6. context_draft (director-authored scene brief, as "user" message)
"""
from __future__ import annotations

import logging
from typing import Optional

import tiktoken

from app.constants import DEFAULT_RESPONSE_RESERVE
from app.models import Character, Save, Scenario
from app.variables import apply_variables

logger = logging.getLogger(__name__)

_ENCODING = tiktoken.get_encoding("cl100k_base")

# Ollama role mapping
_ROLE_MAP = {
    "user": "user",
    "character": "assistant",
    "dm": "assistant",
}


def _count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def _count_messages_tokens(messages: list[dict[str, str]]) -> int:
    # Approximate: 4 tokens overhead per message (role + separators)
    return sum(_count_tokens(m["content"]) + 4 for m in messages)


def _format_response_examples(
    character: Character,
    user_name: str,
    char_name: str,
) -> str:
    if not character.response_examples:
        return ""
    lines = ["Example exchanges:"]
    for ex in character.response_examples:
        user_line = apply_variables(ex.get("user", ""), user_name, char_name)
        char_line = apply_variables(ex.get("char", ""), user_name, char_name)
        lines.append(f"{user_name}: {user_line}")
        lines.append(f"{char_name}: {char_line}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _build_prefix_messages(
    character: Character,
    scenario: Scenario,
    user_name: str,
    char_name: str,
) -> list[dict[str, str]]:
    """Build the fixed prefix (system messages before chat history)."""
    prefix: list[dict[str, str]] = []

    system_prompt = apply_variables(scenario.system_prompt, user_name, char_name)
    prefix.append({"role": "system", "content": system_prompt})

    description = apply_variables(character.description, user_name, char_name)
    prefix.append({"role": "system", "content": description})

    examples_text = _format_response_examples(character, user_name, char_name)
    if examples_text:
        prefix.append({"role": "system", "content": examples_text})

    if scenario.persistent_messages:
        joined = "\n".join(
            apply_variables(m, user_name, char_name)
            for m in scenario.persistent_messages
        )
        prefix.append({"role": "system", "content": joined})

    return prefix


def _build_truncated_chat(
    save: Save,
    max_tokens: int,
    strip_dm_only: bool = True,
) -> list[dict[str, str]]:
    """
    Convert save.messages to Ollama chat format, strip DM-only messages if
    requested, then truncate oldest messages until they fit within max_tokens.
    The most recent user message is always preserved.
    """
    # Filter and convert
    raw: list[dict[str, str]] = []
    for msg in save.messages:
        if strip_dm_only and msg.is_dm_only:
            continue
        raw.append({
            "role": _ROLE_MAP[msg.role],
            "content": msg.content,
        })

    if not raw:
        return []

    # Find the index of the most recent user message (must never be dropped)
    last_user_idx = None
    for i in range(len(raw) - 1, -1, -1):
        if raw[i]["role"] == "user":
            last_user_idx = i
            break

    # Truncate oldest until within budget
    total = _count_messages_tokens(raw)
    while total > max_tokens and len(raw) > 0:
        # Never drop the most recent user message
        if last_user_idx == 0:
            break
        dropped = raw.pop(0)
        total -= _count_tokens(dropped["content"]) + 4
        if last_user_idx is not None:
            last_user_idx -= 1

    return raw


def build_character_prompt(
    character: Character,
    scenario: Scenario,
    save: Save,
    user_name: str,
    char_name: Optional[str] = None,
    context_draft: str = "",
) -> list[dict[str, str]]:
    """
    Build the full Ollama messages list for a character's turn.

    The character receives its card (system prompt, description, examples, persistent
    messages) plus a director-authored context_draft scene brief instead of the raw
    adventure log.

    char_name defaults to character.name.
    """
    effective_char_name = char_name if char_name is not None else character.name

    messages = _build_prefix_messages(character, scenario, user_name, effective_char_name)

    # Shared output rule for every companion. The roster listing bleeding into output
    # ("Bram Stonefist: \"This axe...\"") and stage directions inside dialogue
    # ("*I look at Kestrel.*") are structural tics, not per-character ones, so this
    # lives here rather than being repeated in each card.
    if not character.is_dm:
        messages.append({
            "role": "system",
            "content": (
                "Reply with spoken dialogue only. Never prefix your reply with your own "
                "name, and never write stage directions or narrate your own actions — "
                "the narrator handles those."
            ),
        })

    # Director-authored scene brief (replaces raw chat log)
    if context_draft:
        messages.append({"role": "user", "content": context_draft})

    logger.debug(
        "Assembled %s prompt: %d messages total (save=%s, char=%s)",
        character.name,
        len(messages),
        save.id,
        character.id,
    )
    if logger.isEnabledFor(logging.DEBUG):
        for i, m in enumerate(messages):
            snippet = m["content"][:120].replace("\n", " ")
            logger.debug("  [%d] role=%s | %s...", i, m["role"], snippet)

    return messages


# ── Director Prompt Assembly constants ────────────────────────────────────────

_DIRECTOR_SYSTEM = (
    "You are the Director of a roleplaying game. Your only job is to decide which "
    "character should speak next based on what just happened. You do not narrate, "
    "write dialogue, or take a character's voice. You return a structured decision."
)

_DIRECTOR_RULE_SPEAKER = (
    "Who, if anyone, speaks next.\n"
    "   - Set speaker_character_id to the character's id value (shown in square brackets in the roster "
    "above), NOT their display name.\n"
    "   - If a companion NPC witnessed, was addressed, or has a clear emotional or story reason to "
    "react this turn, choose them. DM narration and a companion speaking in the same turn is valid "
    "and common — set dm_should_narrate=true AND speaker_character_id to the companion's id.\n"
    "   - Set speaker_character_id to null only when the scene genuinely calls for the player to "
    "act next with no NPC follow-up: the beat is complete, the moment is silent, or no companion "
    "has anything natural to contribute.\n"
    "   - Set dm_should_narrate=true if the scene needs atmospheric narration, regardless of "
    "whether a companion also speaks. When speaker_character_id is null, dm_should_narrate should "
    "almost always be true — otherwise the turn produces nothing.\n"
    "   - If the player addressed a companion by name, asked them a question, or told them to do "
    "something, that companion MUST get speaker_character_id. Do not let the DM answer for them."
)

_DIRECTOR_RULE_BEAT = (
    "Whether the current beat's transition condition has been met. It is a completion test, "
    "not a description of where the party roughly is — the event it names must have actually "
    "happened. Heading toward it, being near it, or talking about it is beat_transition=false, "
    "as is having no beat listed after the current one. If it has been met, set "
    "beat_transition=true and copy next_beat_id exactly from the square brackets in the roster "
    "— never a beat name, an order number, or an id you invented."
)

_DIRECTOR_RULE_NOTE = (
    "Write a brief direction_note (1-2 sentences) for the DM summarizing what "
    "this turn should accomplish narratively."
)


def _build_director_instruction(include_beat_rule: bool) -> str:
    """Assemble the Director's closing instruction.

    The beat rule is omitted when no beat roster was sent (sandbox mode, or a
    scenario with no beats) — otherwise it is ~90 tokens per turn telling the model
    to reason about a roster it cannot see.
    """
    rules = [_DIRECTOR_RULE_SPEAKER]
    if include_beat_rule:
        rules.append(_DIRECTOR_RULE_BEAT)
    rules.append(_DIRECTOR_RULE_NOTE)
    numbered = "\n".join(f"{i}) {rule}\n" for i, rule in enumerate(rules, start=1))
    return (
        "Based on the conversation so far, decide:\n"
        + numbered
        + "Respond in the required JSON format."
    )


def _companion_staleness(save: Save, characters: dict[str, "Character"]) -> str:
    """One line stating how many player turns since each companion last spoke.

    Returns "" before the first player turn, when the number would be meaningless.
    A "turn" is counted as a user message, which is what the player experiences as
    one exchange.
    """
    companions = [
        characters[cid]
        for cid in save.active_character_ids
        if cid in characters and not characters[cid].is_dm
    ]
    if not companions:
        return ""
    if not any(m.role == "user" for m in save.messages):
        return ""

    parts: list[str] = []
    for char in companions:
        turns = 0
        spoken = False
        for msg in reversed(save.messages):
            if msg.character_id == char.id:
                spoken = True
                break
            if msg.role == "user":
                turns += 1
        if not spoken:
            parts.append(f"{char.name}: has not spoken yet")
        elif turns == 0:
            parts.append(f"{char.name}: spoke last turn")
        else:
            parts.append(f"{char.name}: {turns} turns ago")

    return "Turns since each companion last spoke — " + ", ".join(parts) + "."


def build_director_prompt(
    save: Save,
    scenario: Scenario,
    characters: dict[str, "Character"],
    favored_character_ids: list[str] | None = None,
    response_reserve: int = DEFAULT_RESPONSE_RESERVE,
) -> list[dict[str, str]]:
    """
    Build the full Ollama messages list for the Director's routing call.
    Excludes scenario.system_prompt, DM description/examples, and beat description/starter_prompt.
    Director sees DM-only messages and dm_only_info (full context for routing decisions).
    """
    from app.models import Character  # local import avoids circular reference

    prefix: list[dict[str, str]] = []

    # 1. Neutral director system prompt
    prefix.append({"role": "system", "content": _DIRECTOR_SYSTEM})

    # 2. Scenario orientation
    scenario_summary = scenario.summary or scenario.initial_message[:200]
    prefix.append({
        "role": "system",
        "content": f"Scenario: {scenario.name}\n{scenario_summary}",
    })

    # 3. Character roster
    roster_lines: list[str] = []
    for cid in save.active_character_ids:
        char = characters.get(cid)
        if char is None:
            continue
        role_label = "DM" if char.is_dm else "companion"
        summary = char.description_summary or char.description[:150]
        roster_lines.append(f"- {char.name} [id: {char.id}] ({role_label}): {summary}")
    if roster_lines:
        prefix.append({
            "role": "system",
            "content": "Active characters:\n" + "\n".join(roster_lines),
        })

    # 3b. Favored character hint (player preference for this turn)
    if favored_character_ids:
        favored_names = [
            characters[cid].name
            for cid in favored_character_ids
            if cid in characters
        ]
        if favored_names:
            names_str = ", ".join(favored_names)
            prefix.append({
                "role": "system",
                "content": (
                    f"The player has indicated a preference for hearing from: {names_str}. "
                    "Consider biasing toward one of these characters this turn."
                ),
            })

    # 3c. Companion staleness — how long since each has had a line. Exhortation alone
    # was not moving the Director off speaker_character_id=null, so give it the fact.
    staleness = _companion_staleness(save, characters)
    if staleness:
        prefix.append({"role": "system", "content": staleness})

    # 4. Beat roster (only if beats exist and not sandbox_mode)
    beat_roster_sent = False
    if scenario.beats and not save.sandbox_mode:
        beats_by_id = {b.id: b for b in scenario.beats}
        current_order = -1
        if save.current_beat_id and save.current_beat_id in beats_by_id:
            current_order = beats_by_id[save.current_beat_id].order

        beat_lines: list[str] = []
        for beat in sorted(scenario.beats, key=lambda b: b.order):
            if beat.order < current_order:
                continue  # skip past beats
            marker = "[CURRENT] " if beat.id == save.current_beat_id else ""
            summary = beat.summary or beat.name
            beat_lines.append(
                f"- {marker}Beat {beat.order}: {beat.name} [id: {beat.id}] — {summary}. "
                f"Transition: {beat.transition_condition}"
            )

        if beat_lines:
            # On the last beat the model reliably invents a next_beat_id rather than
            # declining, so say the terminal case outright instead of leaving it to
            # be inferred from the roster having no further entries.
            is_terminal = bool(save.current_beat_id) and not any(
                b.order > current_order for b in scenario.beats
            )
            footer = (
                "\nThis is the final beat. There is no next beat and none can be "
                "invented — set beat_transition=false."
                if is_terminal
                else "\nYou may signal a transition to the next beat OR skip ahead to a later "
                "beat if the story has clearly moved past intervening events. You cannot go backward."
            )
            prefix.append({
                "role": "system",
                "content": (
                    "The story is structured in the following beats:\n"
                    + "\n".join(beat_lines)
                    + footer
                ),
            })
            beat_roster_sent = True

    # 5. Persistent messages
    if scenario.persistent_messages:
        joined = "\n".join(scenario.persistent_messages)
        prefix.append({"role": "system", "content": joined})

    # 6. DM-only info (Director has full visibility)
    if scenario.dm_only_info:
        joined = "\n".join(scenario.dm_only_info)
        prefix.append({"role": "system", "content": joined})

    # 7. Truncated shared chat (Director sees all messages including DM-only)
    prefix_tokens = _count_messages_tokens(prefix)
    available_chat_tokens = save.max_context_tokens - prefix_tokens - response_reserve

    logger.debug(
        "Director token budget: prefix=%d, available_chat=%d (save=%s)",
        prefix_tokens,
        available_chat_tokens,
        save.id,
    )

    chat = _build_truncated_chat(save, max_tokens=max(0, available_chat_tokens), strip_dm_only=False)

    # 8. Hardcoded instruction (always last)
    instruction_msg = {
        "role": "system",
        "content": _build_director_instruction(include_beat_rule=beat_roster_sent),
    }
    messages = prefix + chat + [instruction_msg]

    logger.debug(
        "Assembled Director prompt: %d messages total (save=%s)",
        len(messages),
        save.id,
    )
    if logger.isEnabledFor(logging.DEBUG):
        for i, m in enumerate(messages):
            snippet = m["content"][:120].replace("\n", " ")
            logger.debug("  [%d] role=%s | %s...", i, m["role"], snippet)

    return messages


_DRAFT_INSTRUCTIONS: dict[str, str] = {
    "narrator": (
        "Draft a 2-4 sentence scene brief for {name} the narrator. "
        "Summarize what just happened in the scene, what atmospheric moment or story beat they should narrate, "
        "and the tone. Write as a direct instruction to the narrator."
    ),
    "companion": (
        "Draft a 2-4 sentence scene brief for {name}. "
        "Summarize what they just witnessed or heard, what they should respond to, "
        "and the emotional/social register of their response. Write as a direct instruction to {name}."
    ),
    "options": (
        "Draft a 2-4 sentence situation summary for player option generation. "
        "Describe the player's current situation, what just occurred, "
        "and what meaningful choices face them next."
    ),
}


def build_director_draft_prompt(
    save: Save,
    scenario: Scenario,
    characters: dict[str, "Character"],
    target_name: str,
    target_role: str,
    direction_note: Optional[str] = None,
    response_reserve: int = DEFAULT_RESPONSE_RESERVE,
) -> list[dict[str, str]]:
    """
    Build the Ollama messages list for a Director context-drafting call (Phase 1.5).

    The Director sees the full chat log and drafts an isolated scene brief for the
    target persona. target_role must be one of: "narrator", "companion", "options".
    """
    prefix: list[dict[str, str]] = []

    # 1. Neutral director system prompt
    prefix.append({"role": "system", "content": _DIRECTOR_SYSTEM})

    # 2. Scenario orientation
    scenario_summary = scenario.summary or scenario.initial_message[:200]
    prefix.append({
        "role": "system",
        "content": f"Scenario: {scenario.name}\n{scenario_summary}",
    })

    # 3. Character roster
    roster_lines: list[str] = []
    for cid in save.active_character_ids:
        char = characters.get(cid)
        if char is None:
            continue
        role_label = "DM" if char.is_dm else "companion"
        summary = char.description_summary or char.description[:150]
        roster_lines.append(f"- {char.name} [id: {char.id}] ({role_label}): {summary}")
    if roster_lines:
        prefix.append({
            "role": "system",
            "content": "Active characters:\n" + "\n".join(roster_lines),
        })

    # 4. Truncated shared chat (Director sees all, including DM-only)
    prefix_tokens = _count_messages_tokens(prefix)
    available_chat_tokens = save.max_context_tokens - prefix_tokens - response_reserve
    chat = _build_truncated_chat(save, max_tokens=max(0, available_chat_tokens), strip_dm_only=False)

    # 5. Drafting instruction (always last)
    template = _DRAFT_INSTRUCTIONS.get(target_role, _DRAFT_INSTRUCTIONS["companion"])
    instruction = template.format(name=target_name)
    if direction_note:
        instruction += f"\n\nDirector routing note (use as context): {direction_note}"

    instruction_msg = {"role": "system", "content": instruction}
    messages = prefix + chat + [instruction_msg]

    logger.debug(
        "Assembled Director draft prompt: %d messages total (save=%s, target=%s, role=%s)",
        len(messages),
        save.id,
        target_name,
        target_role,
    )

    return messages


def build_dm_prompt(
    save: Save,
    scenario: Scenario,
    dm_character: "Character",
    context_draft: str = "",
    companion_names: Optional[list[str]] = None,
) -> list[dict[str, str]]:
    """
    Build the full Ollama messages list for DM narration (Phase 2) or option
    drafting (Phase 3). The DM receives its character card plus a director-authored
    context_draft scene brief instead of the raw adventure log.
    """
    user_name = save.user_name
    char_name = dm_character.name
    messages: list[dict[str, str]] = []

    # 1. Scenario system prompt
    system_prompt = apply_variables(scenario.system_prompt, user_name, char_name)
    messages.append({"role": "system", "content": system_prompt})

    # 2. DM character description
    description = apply_variables(dm_character.description, user_name, char_name)
    messages.append({"role": "system", "content": description})

    # 2b. Exclusion rule: the DM narrates the world, not the people in it. The old
    # "describe their actions from the outside" permission is what produced
    # "As Silvaine rises, you feel a chill..." on a turn where the player had asked
    # Silvaine to act — she became scenery instead of a character.
    exclusion_names = list(companion_names or []) + [user_name]
    names_str = ", ".join(exclusion_names)
    messages.append({
        "role": "system",
        "content": (
            f"IMPORTANT: You narrate the world, not the people in it. Do not decide what "
            f"{names_str} say, do, or choose — no dialogue for them, no actions on their "
            f"behalf, no moving them around the scene. Each of them acts in their own turn. "
            f"Narrate only what {user_name} has already stated, and the world's response to it."
        ),
    })

    # 3. DM response examples
    examples_text = _format_response_examples(dm_character, user_name, char_name)
    if examples_text:
        messages.append({"role": "system", "content": examples_text})

    # 4. Persistent messages
    if scenario.persistent_messages:
        joined = "\n".join(
            apply_variables(m, user_name, char_name) for m in scenario.persistent_messages
        )
        messages.append({"role": "system", "content": joined})

    # 5. DM-only info
    if scenario.dm_only_info:
        joined = "\n".join(
            apply_variables(m, user_name, char_name) for m in scenario.dm_only_info
        )
        messages.append({"role": "system", "content": joined})

    # 6. Active beat context
    if save.current_beat_id:
        beats_by_id = {b.id: b for b in scenario.beats}
        active_beat = beats_by_id.get(save.current_beat_id)
        if active_beat:
            beat_context = f"Current scene: {active_beat.name}\n{active_beat.description}"
            messages.append({"role": "system", "content": beat_context})

    # 7. Director-authored scene brief (replaces raw chat log)
    if context_draft:
        messages.append({"role": "user", "content": context_draft})

    logger.debug(
        "Assembled DM prompt: %d messages total (save=%s)",
        len(messages),
        save.id,
    )
    if logger.isEnabledFor(logging.DEBUG):
        for i, m in enumerate(messages):
            snippet = m["content"][:120].replace("\n", " ")
            logger.debug("  [%d] role=%s | %s...", i, m["role"], snippet)

    return messages
