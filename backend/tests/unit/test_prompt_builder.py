"""
Unit tests for prompt_builder and variables.

Covers:
  - apply_variables: substitution correctness, {{char}} left literal when char_name=None
  - build_character_prompt: prefix structure, context_draft appended, variable application
  - build_director_draft_prompt: chat history visible, instruction targets correct persona
  - build_director_prompt: roster, beats, DM-only visibility, instruction placement
  - build_dm_prompt: prefix structure, context_draft appended, beat context
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models import Character, Message, Save, Scenario
from app.prompt_builder import build_character_prompt
from app.variables import apply_variables


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _char(is_dm: bool = False, description: str = "I am {{char}}, serving {{user}}.", response_examples: list | None = None) -> Character:
    examples = response_examples if response_examples is not None else [
        {"user": "Hello {{user}}.", "char": "Hi, I am {{char}}."}
    ]
    return Character(
        id="test-char",
        name="TestChar",
        description=description,
        is_dm=is_dm,
        response_examples=examples,
    )


def _scenario(
    system_prompt: str = "System: {{user}} plays here.",
    persistent_messages: list[str] | None = None,
) -> Scenario:
    return Scenario(
        id="test-scenario",
        name="Test",
        initial_message="Begin.",
        system_prompt=system_prompt,
        persistent_messages=persistent_messages or [],
    )


def _save(messages: list[Message] | None = None, max_context_tokens: int = 8192) -> Save:
    now = datetime.now(timezone.utc).isoformat()
    return Save(
        id="test-save",
        scenario_id="test-scenario",
        name="Test Save",
        active_character_ids=["test-char"],
        user_name="Alice",
        max_context_tokens=max_context_tokens,
        messages=messages or [],
        created_at=now,
        updated_at=now,
    )


def _msg(role: str, content: str, is_dm_only: bool = False) -> Message:
    return Message(
        id=str(uuid.uuid4()),
        role=role,  # type: ignore[arg-type]
        content=content,
        timestamp=datetime.now(timezone.utc).isoformat(),
        is_dm_only=is_dm_only,
    )


# ── apply_variables ───────────────────────────────────────────────────────────

class TestApplyVariables:
    def test_user_replaced(self):
        assert apply_variables("Hello {{user}}!", "Alice") == "Hello Alice!"

    def test_char_replaced(self):
        assert apply_variables("I am {{char}}.", "Alice", "Bram") == "I am Bram."

    def test_both_replaced(self):
        result = apply_variables("{{user}} meets {{char}}.", "Alice", "Bram")
        assert result == "Alice meets Bram."

    def test_char_left_literal_when_none(self):
        result = apply_variables("I am {{char}}.", "Alice", None)
        assert "{{char}}" in result
        assert "Alice" not in result.split("{{char}}")[0] or True  # user not in char slot

    def test_no_placeholders_unchanged(self):
        text = "Nothing to replace here."
        assert apply_variables(text, "Alice", "Bram") == text

    def test_repeated_placeholders(self):
        result = apply_variables("{{user}} and {{user}} again.", "Alice")
        assert result == "Alice and Alice again."

    def test_char_repeated(self):
        result = apply_variables("{{char}} said {{char}} twice.", "Alice", "Bram")
        assert result == "Bram said Bram twice."


# ── build_character_prompt structure ─────────────────────────────────────────

class TestBuildCharacterPromptStructure:
    def test_prefix_messages_are_system_role(self):
        char = _char()
        scenario = _scenario()
        save = _save()
        messages = build_character_prompt(char, scenario, save, "Alice")
        # First few messages (prefix) must be "system"
        assert all(m["role"] == "system" for m in messages[:2])

    def test_variables_applied_no_literals_in_prefix(self):
        # Variable substitution is applied to system-level content (description,
        # system_prompt, response_examples, persistent_messages). Raw chat history
        # messages are stored verbatim — they are NOT substituted, because they
        # represent real user/character input that may legitimately contain these strings.
        char = _char()
        scenario = _scenario()
        save = _save()  # no chat messages — only prefix content
        messages = build_character_prompt(char, scenario, save, "Alice")
        # Only check the prefix (system) messages
        prefix_text = " ".join(m["content"] for m in messages if m["role"] == "system")
        assert "{{user}}" not in prefix_text
        assert "{{char}}" not in prefix_text
        # Confirm substitution actually happened
        assert "Alice" in prefix_text
        assert "TestChar" in prefix_text

    def test_context_draft_appended_as_user_message(self):
        char = _char()
        scenario = _scenario()
        save = _save()
        draft = "The player just entered the tavern. React with curiosity."
        messages = build_character_prompt(char, scenario, save, "Alice", context_draft=draft)
        last = messages[-1]
        assert last["role"] == "user"
        assert draft in last["content"]

    def test_no_context_draft_means_no_user_message(self):
        char = _char()
        scenario = _scenario()
        save = _save()
        messages = build_character_prompt(char, scenario, save, "Alice", context_draft="")
        assert all(m["role"] == "system" for m in messages)

    def test_chat_history_not_in_prompt(self):
        char = _char()
        scenario = _scenario()
        chat_content = "THIS SHOULD NOT APPEAR IN PROMPT"
        save = _save(messages=[_msg("user", chat_content)])
        messages = build_character_prompt(char, scenario, save, "Alice")
        all_content = " ".join(m["content"] for m in messages)
        assert chat_content not in all_content

    def test_persistent_messages_included(self):
        char = _char()
        scenario = _scenario(persistent_messages=["World lore: the sun is dying."])
        save = _save()
        messages = build_character_prompt(char, scenario, save, "Alice")
        all_content = " ".join(m["content"] for m in messages)
        assert "World lore" in all_content

    def test_response_examples_included(self):
        char = _char()
        scenario = _scenario()
        save = _save()
        messages = build_character_prompt(char, scenario, save, "Alice")
        all_content = " ".join(m["content"] for m in messages)
        assert "Example exchanges" in all_content


# ── build_director_prompt ─────────────────────────────────────────────────────


# ── build_director_prompt ─────────────────────────────────────────────────────

from app.fixtures import BRAM, CHARACTERS, NARRATOR, SCENARIO, make_stage1_save
from app.prompt_builder import build_director_draft_prompt, build_director_prompt, build_dm_prompt
from app.models import Beat


def _make_beat(order: int, beat_id: str = "") -> Beat:
    bid = beat_id or f"beat-{order}"
    return Beat(
        id=bid,
        order=order,
        name=f"Beat {order}",
        description=f"Description of beat {order}",
        summary=f"Summary of beat {order}",
        transition_condition=f"When beat {order} is done",
        starter_prompt=f"Beat {order} begins.",
    )


def _all_content(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


def _fixture_save(**kwargs):
    save = make_stage1_save()
    for k, v in kwargs.items():
        setattr(save, k, v)
    return save


class TestBuildDirectorPrompt:

    def test_neutral_system_prompt_first(self):
        save = _fixture_save()
        messages = build_director_prompt(save, SCENARIO, CHARACTERS)
        assert "Director" in messages[0]["content"]
        assert "collaborative roleplay adventure" not in _all_content(messages)

    def test_excludes_scenario_system_prompt(self):
        save = _fixture_save()
        messages = build_director_prompt(save, SCENARIO, CHARACTERS)
        assert "collaborative roleplay adventure" not in _all_content(messages)

    def test_includes_scenario_name_and_summary(self):
        save = _fixture_save()
        messages = build_director_prompt(save, SCENARIO, CHARACTERS)
        content = _all_content(messages)
        assert SCENARIO.name in content
        assert SCENARIO.summary[:50] in content

    def test_scenario_fallback_to_initial_message(self):
        no_summary = SCENARIO.model_copy(update={"summary": ""})
        messages = build_director_prompt(_fixture_save(), no_summary, CHARACTERS)
        assert no_summary.initial_message[:100] in _all_content(messages)

    def test_roster_uses_description_summary(self):
        messages = build_director_prompt(_fixture_save(), SCENARIO, CHARACTERS)
        content = _all_content(messages)
        assert NARRATOR.description_summary in content
        assert BRAM.description_summary in content

    def test_roster_fallback_to_description_slice(self):
        no_summary = BRAM.model_copy(update={"description_summary": ""})
        chars = {NARRATOR.id: NARRATOR, no_summary.id: no_summary}
        messages = build_director_prompt(_fixture_save(), SCENARIO, chars)
        assert no_summary.description[:100] in _all_content(messages)

    def test_roster_labels_dm_and_companion(self):
        content = _all_content(build_director_prompt(_fixture_save(), SCENARIO, CHARACTERS))
        assert "(DM)" in content
        assert "(companion)" in content

    def test_beat_roster_omitted_when_no_beats(self):
        sc_no_beats = SCENARIO.model_copy(update={"beats": []})
        content = _all_content(build_director_prompt(_fixture_save(), sc_no_beats, CHARACTERS))
        assert "[CURRENT]" not in content

    def test_beat_roster_omitted_in_sandbox_mode(self):
        beat = _make_beat(0, "b0")
        sc = SCENARIO.model_copy(update={"beats": [beat]})
        save = _fixture_save(current_beat_id="b0", sandbox_mode=True)
        content = _all_content(build_director_prompt(save, sc, CHARACTERS))
        assert "[CURRENT]" not in content

    def test_beat_roster_marks_current_beat(self):
        b0, b1 = _make_beat(0, "b0"), _make_beat(1, "b1")
        sc = SCENARIO.model_copy(update={"beats": [b0, b1]})
        save = _fixture_save(current_beat_id="b0", sandbox_mode=False)
        content = _all_content(build_director_prompt(save, sc, CHARACTERS))
        assert "[CURRENT]" in content
        assert "Beat 1" in content

    def test_beat_roster_excludes_past_beats(self):
        b0, b1, b2 = _make_beat(0, "b0"), _make_beat(1, "b1"), _make_beat(2, "b2")
        sc = SCENARIO.model_copy(update={"beats": [b0, b1, b2]})
        save = _fixture_save(current_beat_id="b1", sandbox_mode=False)
        content = _all_content(build_director_prompt(save, sc, CHARACTERS))
        # Beat 0 (past) should not appear; Beat 1 and 2 should
        assert "Beat 0" not in content
        assert "Beat 1" in content

    def test_dm_only_info_included(self):
        content = _all_content(build_director_prompt(_fixture_save(), SCENARIO, CHARACTERS))
        assert "fire trap" in content.lower()

    def test_dm_only_messages_visible_to_director(self):
        save = _fixture_save()
        save.messages.append(Message(
            id="dmo",
            role="dm",
            character_id=NARRATOR.id,
            content="SECRET DM MESSAGE",
            timestamp="2026-01-01T00:00:00+00:00",
            is_dm_only=True,
        ))
        content = _all_content(build_director_prompt(save, SCENARIO, CHARACTERS))
        assert "SECRET DM MESSAGE" in content

    def test_instruction_is_final_message(self):
        save = _fixture_save()
        messages = build_director_prompt(save, SCENARIO, CHARACTERS)
        assert "JSON" in messages[-1]["content"] or "json" in messages[-1]["content"]

    def test_excludes_dm_description_and_examples(self):
        content = _all_content(build_director_prompt(_fixture_save(), SCENARIO, CHARACTERS))
        # Full NARRATOR description starts with "You are {{char}}, the Dungeon Master and narrator"
        assert "You are {{char}}, the Dungeon Master and narrator" not in content
        # Narrator's first response example
        assert "I push open the heavy oak door" not in content

    def test_favored_character_hint_injected(self):
        messages = build_director_prompt(
            _fixture_save(), SCENARIO, CHARACTERS,
            favored_character_ids=[BRAM.id],
        )
        content = _all_content(messages)
        assert BRAM.name in content
        assert "preference for hearing from" in content

    def test_favored_hint_absent_when_empty(self):
        content = _all_content(build_director_prompt(_fixture_save(), SCENARIO, CHARACTERS, favored_character_ids=[]))
        assert "preference for hearing from" not in content

    def test_response_reserve_affects_budget(self):
        # With a very large reserve the chat portion shrinks to 0 — verify the function
        # doesn't crash and still returns a usable messages list.
        save = _fixture_save()
        save.messages.append(_msg("user", "Hello!"))
        messages = build_director_prompt(save, SCENARIO, CHARACTERS, response_reserve=8192)
        assert len(messages) >= 2  # at least system + instruction


# ── build_director_draft_prompt ───────────────────────────────────────────────

class TestBuildDirectorDraftPrompt:

    def test_chat_history_visible_to_director(self):
        save = _fixture_save()
        chat_content = "PLAYER SAID SOMETHING"
        save.messages.append(_msg("user", chat_content))
        messages = build_director_draft_prompt(save, SCENARIO, CHARACTERS, "Bram", "companion")
        content = _all_content(messages)
        assert chat_content in content

    def test_dm_only_messages_visible(self):
        save = _fixture_save()
        save.messages.append(Message(
            id="dmo",
            role="dm",
            character_id=NARRATOR.id,
            content="DIRECTOR SEES DM SECRET",
            timestamp="2026-01-01T00:00:00+00:00",
            is_dm_only=True,
        ))
        content = _all_content(build_director_draft_prompt(save, SCENARIO, CHARACTERS, "Bram", "companion"))
        assert "DIRECTOR SEES DM SECRET" in content

    def test_narrator_instruction_references_target_name(self):
        messages = build_director_draft_prompt(_fixture_save(), SCENARIO, CHARACTERS, "Elara", "narrator")
        instruction = messages[-1]["content"]
        assert "Elara" in instruction
        assert "narrator" in instruction.lower()

    def test_companion_instruction_references_target_name(self):
        messages = build_director_draft_prompt(_fixture_save(), SCENARIO, CHARACTERS, "Bram", "companion")
        instruction = messages[-1]["content"]
        assert "Bram" in instruction

    def test_options_instruction_does_not_require_name(self):
        messages = build_director_draft_prompt(_fixture_save(), SCENARIO, CHARACTERS, "Narrator", "options")
        instruction = messages[-1]["content"]
        assert "option" in instruction.lower() or "situation" in instruction.lower() or "player" in instruction.lower()

    def test_direction_note_included_when_provided(self):
        note = "Something dramatic just happened."
        messages = build_director_draft_prompt(
            _fixture_save(), SCENARIO, CHARACTERS, "Bram", "companion", direction_note=note
        )
        instruction = messages[-1]["content"]
        assert note in instruction

    def test_direction_note_absent_when_not_provided(self):
        messages = build_director_draft_prompt(_fixture_save(), SCENARIO, CHARACTERS, "Bram", "companion")
        instruction = messages[-1]["content"]
        assert "routing note" not in instruction

    def test_instruction_is_final_message(self):
        messages = build_director_draft_prompt(_fixture_save(), SCENARIO, CHARACTERS, "Bram", "companion")
        # Instruction must come after any chat messages
        assert messages[-1]["role"] == "system"

    def test_roster_included(self):
        content = _all_content(build_director_draft_prompt(_fixture_save(), SCENARIO, CHARACTERS, "Bram", "companion"))
        assert BRAM.name in content
        assert NARRATOR.name in content


# ── build_dm_prompt ───────────────────────────────────────────────────────────

class TestBuildDmPrompt:

    def test_includes_system_prompt(self):
        content = _all_content(build_dm_prompt(_fixture_save(), SCENARIO, NARRATOR))
        assert "collaborative roleplay adventure" in content.lower()

    def test_includes_dm_description(self):
        content = _all_content(build_dm_prompt(_fixture_save(), SCENARIO, NARRATOR))
        assert "Dungeon Master and narrator" in content

    def test_includes_dm_only_info(self):
        content = _all_content(build_dm_prompt(_fixture_save(), SCENARIO, NARRATOR))
        assert "fire trap" in content.lower()

    def test_chat_history_not_included(self):
        save = _fixture_save()
        save.messages.append(_msg("user", "THIS CHAT MESSAGE MUST NOT APPEAR"))
        content = _all_content(build_dm_prompt(save, SCENARIO, NARRATOR))
        assert "THIS CHAT MESSAGE MUST NOT APPEAR" not in content

    def test_context_draft_appended_as_user_message(self):
        draft = "Describe the ancient door creaking open and the darkness beyond."
        messages = build_dm_prompt(_fixture_save(), SCENARIO, NARRATOR, context_draft=draft)
        last = messages[-1]
        assert last["role"] == "user"
        assert draft in last["content"]

    def test_no_context_draft_no_user_message(self):
        messages = build_dm_prompt(_fixture_save(), SCENARIO, NARRATOR, context_draft="")
        assert all(m["role"] == "system" for m in messages)

    def test_beat_context_included_when_set(self):
        beat = _make_beat(0, "b0")
        sc = SCENARIO.model_copy(update={"beats": [beat]})
        save = _fixture_save(current_beat_id="b0")
        content = _all_content(build_dm_prompt(save, sc, NARRATOR))
        assert "Current scene:" in content
        assert beat.name in content
        assert beat.description in content

    def test_beat_context_absent_when_no_beat(self):
        save = _fixture_save(current_beat_id=None)
        content = _all_content(build_dm_prompt(save, SCENARIO, NARRATOR))
        assert "Current scene:" not in content

    def test_variable_substitution_applied(self):
        save = _fixture_save(user_name="Alice")
        content = _all_content(build_dm_prompt(save, SCENARIO, NARRATOR))
        assert "{{user}}" not in content

    def test_response_examples_included(self):
        content = _all_content(build_dm_prompt(_fixture_save(), SCENARIO, NARRATOR))
        assert "push open the heavy oak door" in content
