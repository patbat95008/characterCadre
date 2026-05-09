"""
Debug endpoints — not intended for production use.
Provides prompt inspection without requiring LOG_LEVEL=DEBUG.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import storage
from app.prompt_builder import build_character_prompt

router = APIRouter()


@router.get("/debug/prompt-preview")
def prompt_preview(save_id: str, character_id: str) -> dict:
    """
    Return the assembled prompt that would be sent to Ollama for the given
    character against the given save's current state.

    Use this to verify {{user}}/{{char}} substitution and prompt structure
    without needing LOG_LEVEL=DEBUG.
    """
    save = storage.get_save(save_id)
    if save is None:
        raise HTTPException(404, f"Save {save_id} not found")
    scenario = storage.get_scenario(save.scenario_id)
    if scenario is None:
        raise HTTPException(404, f"Scenario {save.scenario_id} not found")
    character = storage.get_character(character_id)
    if character is None:
        raise HTTPException(404, f"Character {character_id} not found")

    messages = build_character_prompt(character, scenario, save, save.user_name)
    return {
        "character_id": character.id,
        "character_name": character.name,
        "user_name": save.user_name,
        "save_id": save.id,
        "message_count_in_save": len(save.messages),
        "prompt_messages": messages,
        "prompt_message_count": len(messages),
    }
