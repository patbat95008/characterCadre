from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: Literal["user", "character", "dm"]
    character_id: Optional[str] = None
    content: str
    timestamp: str  # iso8601
    is_dm_only: bool = False
    beat_id_at_time: Optional[str] = None


class Character(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    description_summary: str = ""
    description_hash: str = ""
    response_examples: list[dict[str, str]] = []  # [{user: str, char: str}]
    is_dm: bool = False
    avatar_path: Optional[str] = None


class Beat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    order: int
    name: str
    description: str
    summary: str = ""
    summary_hash: str = ""
    transition_condition: str
    starter_prompt: str


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    summary: str = ""
    summary_hash: str = ""
    initial_message: str
    system_prompt: str
    persistent_messages: list[str] = []
    dm_only_info: list[str] = []
    recommended_character_ids: list[str] = []
    beats: list[Beat] = []


class Save(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    scenario_id: str
    name: str
    active_character_ids: list[str]
    user_name: str
    current_beat_id: Optional[str] = None
    sandbox_mode: bool = False
    messages: list[Message] = []
    max_context_tokens: int = 8192
    created_at: str  # iso8601
    updated_at: str  # iso8601


# ── Route I/O models ──────────────────────────────────────────────────────────

class DiceSpec(BaseModel):
    """Dice roll specification embedded in a player option or turn request."""
    model_config = ConfigDict(extra="forbid")

    dice: Literal["D20", "D100"]
    difficulty: Literal["Easy", "Medium", "Hard"]


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str
    save_id: str = "stage1"
    favored_character_ids: list[str] = []
    response_reserve: int = 1024
    max_response_tokens: Optional[int] = None
    beat_advance: bool = False
    dice_roll: Optional[DiceSpec] = None


class DirectorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_character_id: Optional[str] = None
    dm_should_narrate: bool
    beat_transition: bool
    next_beat_id: Optional[str] = None
    direction_note: str
    reasoning: str = ""
