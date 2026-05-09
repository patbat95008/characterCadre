"""
Save CRUD plus manual beat advance and sandbox-mode toggle.

The chat-turn endpoint (chat.py) loads saves through this same module, so all
save mutations go through `storage.save_save` and are atomic on disk.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import storage
from app.models import Message, Save
from app.variables import apply_variables

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Response shapes ───────────────────────────────────────────────────────────

class SaveSummaryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    scenario_id: str
    scenario_name: str
    message_count: int
    current_beat_id: Optional[str]
    current_beat_name: Optional[str]
    sandbox_mode: bool
    created_at: str
    updated_at: str


class CreateSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    active_character_ids: list[str]
    user_name: str
    name: Optional[str] = None


class UpdateSaveRequest(BaseModel):
    """Patch-style update — every field is optional, only provided ones apply."""
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    max_context_tokens: Optional[int] = Field(default=None, ge=512, le=128_000)


class AdvanceBeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_beat_id: str
    wipe_context: bool = False


class SandboxModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class DeleteResult(BaseModel):
    deleted: bool


# ── List / get ────────────────────────────────────────────────────────────────

@router.get("/saves", response_model=list[SaveSummaryRow])
def list_saves_route() -> list[SaveSummaryRow]:
    rows: list[SaveSummaryRow] = []
    for save in storage.list_saves():
        scenario = storage.get_scenario(save.scenario_id)
        scenario_name = scenario.name if scenario else "(missing scenario)"
        current_beat_name: Optional[str] = None
        if scenario and save.current_beat_id:
            for beat in scenario.beats:
                if beat.id == save.current_beat_id:
                    current_beat_name = beat.name
                    break
        rows.append(
            SaveSummaryRow(
                id=save.id,
                name=save.name,
                scenario_id=save.scenario_id,
                scenario_name=scenario_name,
                message_count=len(save.messages),
                current_beat_id=save.current_beat_id,
                current_beat_name=current_beat_name,
                sandbox_mode=save.sandbox_mode,
                created_at=save.created_at,
                updated_at=save.updated_at,
            )
        )
    rows.sort(key=lambda r: r.updated_at, reverse=True)
    return rows


@router.get("/saves/{save_id}", response_model=Save)
def get_save_route(save_id: str) -> Save:
    save = storage.get_save(save_id)
    if save is None:
        raise HTTPException(404, "Save not found")
    return save


# ── Create ────────────────────────────────────────────────────────────────────

@router.post("/saves", response_model=Save)
def create_save_route(payload: CreateSaveRequest) -> Save:
    scenario = storage.get_scenario(payload.scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")

    # Validate exactly one DM in the active roster
    dm_count = 0
    for cid in payload.active_character_ids:
        c = storage.get_character(cid)
        if c is None:
            raise HTTPException(400, f"Character {cid} not found")
        if c.is_dm:
            dm_count += 1
    if dm_count != 1:
        raise HTTPException(
            400,
            f"Save must have exactly one DM in active_character_ids (got {dm_count})",
        )

    now = datetime.now(timezone.utc).isoformat()
    save_id = str(uuid.uuid4())

    # Seed opening message — always use initial_message regardless of beats.
    # Beats are additive story structure; the Director activates them during play.
    opening_content = apply_variables(
        scenario.initial_message, payload.user_name, char_name=None
    )
    opening_beat_id: Optional[str] = None
    current_beat_id: Optional[str] = None

    opening = Message(
        id=str(uuid.uuid4()),
        role="dm",
        character_id=None,
        content=opening_content,
        timestamp=now,
        is_dm_only=False,
        beat_id_at_time=opening_beat_id,
    )

    save = Save(
        id=save_id,
        scenario_id=scenario.id,
        name=payload.name or scenario.name,
        active_character_ids=list(payload.active_character_ids),
        user_name=payload.user_name,
        current_beat_id=current_beat_id,
        sandbox_mode=False,
        messages=[opening],
        max_context_tokens=8192,
        created_at=now,
        updated_at=now,
    )
    storage.save_save(save)
    logger.info(
        "save created (id=%s, scenario=%s, beats=%s)",
        save.id,
        scenario.id,
        "yes" if scenario.beats else "no",
    )
    return save


# ── Update / delete ───────────────────────────────────────────────────────────

@router.put("/saves/{save_id}", response_model=Save)
def update_save_route(save_id: str, patch: UpdateSaveRequest) -> Save:
    save = storage.get_save(save_id)
    if save is None:
        raise HTTPException(404, "Save not found")
    if patch.name is not None:
        save.name = patch.name
    if patch.max_context_tokens is not None:
        save.max_context_tokens = patch.max_context_tokens
    storage.save_save(save)
    return save


@router.delete("/saves/{save_id}", response_model=DeleteResult)
def delete_save_route(save_id: str) -> DeleteResult:
    deleted = storage.delete_save(save_id)
    if not deleted:
        raise HTTPException(404, "Save not found")
    return DeleteResult(deleted=True)


# ── Manual beat advance ───────────────────────────────────────────────────────

@router.post("/saves/{save_id}/advance-beat", response_model=Save)
def advance_beat_route(save_id: str, payload: AdvanceBeatRequest) -> Save:
    save = storage.get_save(save_id)
    if save is None:
        raise HTTPException(404, "Save not found")
    scenario = storage.get_scenario(save.scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found for save")
    if not scenario.beats:
        raise HTTPException(400, "Scenario has no beats")

    target = next((b for b in scenario.beats if b.id == payload.next_beat_id), None)
    if target is None:
        raise HTTPException(400, f"Beat {payload.next_beat_id} not found in scenario")

    # Forward-only: target.order must be >= current beat's order (when present)
    if save.current_beat_id:
        current = next((b for b in scenario.beats if b.id == save.current_beat_id), None)
        if current is not None and target.order < current.order:
            raise HTTPException(400, "Cannot advance backward")

    starter = apply_variables(target.starter_prompt, save.user_name, char_name=None)
    starter_msg = Message(
        id=str(uuid.uuid4()),
        role="dm",
        character_id=None,
        content=starter,
        timestamp=datetime.now(timezone.utc).isoformat(),
        is_dm_only=False,
        beat_id_at_time=target.id,
    )

    old_beat_name = "none"
    if save.current_beat_id:
        ob = next((b for b in scenario.beats if b.id == save.current_beat_id), None)
        if ob:
            old_beat_name = ob.name

    if payload.wipe_context:
        save.messages = [starter_msg]
        trigger = "manual_hard"
    else:
        save.messages.append(starter_msg)
        trigger = "manual_soft"

    save.current_beat_id = target.id
    save.sandbox_mode = False  # leaving sandbox if a manual advance fires
    storage.save_save(save)
    logger.info(
        "beat transition: %s → %s (save=%s, trigger=%s)",
        old_beat_name,
        target.name,
        save.id,
        trigger,
    )
    return save


# ── Sandbox mode ──────────────────────────────────────────────────────────────

@router.post("/saves/{save_id}/sandbox-mode", response_model=Save)
def set_sandbox_mode_route(save_id: str, payload: SandboxModeRequest) -> Save:
    save = storage.get_save(save_id)
    if save is None:
        raise HTTPException(404, "Save not found")
    save.sandbox_mode = payload.enabled
    storage.save_save(save)
    logger.info("sandbox mode set (save=%s, enabled=%s)", save.id, payload.enabled)
    return save
