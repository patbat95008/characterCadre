"""
Character CRUD, SillyTavern import/export, and avatar upload.

Background summary regen is scheduled via FastAPI BackgroundTasks whenever a
description changes (detected by hash inequality). The `description_hash` is
always stamped to the current description on save — the summary just lags
behind for the few seconds it takes the LLM to produce a new one.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from app import silly_tavern, storage, summarizer
from app.models import Character

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_AVATAR_CONTENT = {"image/jpeg", "image/png", "image/webp"}
EXT_FROM_CONTENT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


# ── Response shapes ───────────────────────────────────────────────────────────

class CharacterSummary(BaseModel):
    """Lighter shape for the list view — full descriptions stay on disk."""
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    is_dm: bool
    description_summary: str
    has_avatar: bool


class DeleteResult(BaseModel):
    deleted: bool
    in_use_by_saves: bool


# ── List / get ────────────────────────────────────────────────────────────────

@router.get("/characters", response_model=list[CharacterSummary])
def list_characters_route() -> list[CharacterSummary]:
    return [
        CharacterSummary(
            id=c.id,
            name=c.name,
            is_dm=c.is_dm,
            description_summary=c.description_summary,
            has_avatar=storage.avatar_path_for(c.id) is not None,
        )
        for c in storage.list_characters()
    ]


@router.get("/characters/{character_id}", response_model=Character)
def get_character_route(character_id: str) -> Character:
    character = storage.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")
    return character


# ── Create / update / delete ──────────────────────────────────────────────────

@router.post("/characters", response_model=Character)
def create_character_route(
    payload: Character, background_tasks: BackgroundTasks
) -> Character:
    if not payload.id:
        payload.id = str(uuid.uuid4())
    if storage.get_character(payload.id) is not None:
        raise HTTPException(409, "Character with this id already exists")

    summarizer.stamp_character_hash(payload)
    storage.save_character(payload)
    background_tasks.add_task(summarizer.regen_character_if_stale, payload.id)
    return payload


@router.put("/characters/{character_id}", response_model=Character)
def update_character_route(
    character_id: str, payload: Character, background_tasks: BackgroundTasks
) -> Character:
    if payload.id != character_id:
        raise HTTPException(400, "Path id and body id must match")
    existing = storage.get_character(character_id)
    if existing is None:
        raise HTTPException(404, "Character not found")

    new_hash = summarizer.character_description_hash(payload.description)
    description_changed = new_hash != existing.description_hash

    # Preserve existing summary if the user hasn't manually changed it AND the
    # description didn't change. If description changed, the background task
    # will regenerate.
    if not description_changed and not payload.description_summary:
        payload.description_summary = existing.description_summary
    payload.description_hash = new_hash

    storage.save_character(payload)
    if description_changed:
        background_tasks.add_task(summarizer.regen_character_if_stale, character_id)
    return payload


@router.delete("/characters/{character_id}", response_model=DeleteResult)
def delete_character_route(character_id: str) -> DeleteResult:
    in_use = storage.is_character_in_use(character_id)
    deleted = storage.delete_character(character_id)
    if not deleted:
        raise HTTPException(404, "Character not found")
    if in_use:
        logger.warning(
            "Deleted character %s but it is still referenced by at least one save",
            character_id,
        )
    return DeleteResult(deleted=True, in_use_by_saves=in_use)


# ── Manual summary regeneration ───────────────────────────────────────────────

class RegenResult(BaseModel):
    summary: str


@router.post("/characters/{character_id}/regenerate-summary", response_model=RegenResult)
async def regenerate_character_summary_route(character_id: str) -> RegenResult:
    summary = await summarizer.regenerate_character_summary_sync(character_id)
    if summary is None:
        raise HTTPException(404, "Character not found")
    return RegenResult(summary=summary)


# ── Avatar upload ─────────────────────────────────────────────────────────────

class AvatarResult(BaseModel):
    avatar_path: str


@router.post("/characters/{character_id}/avatar", response_model=AvatarResult)
async def upload_avatar_route(
    character_id: str, file: UploadFile = File(...)
) -> AvatarResult:
    character = storage.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")

    if file.content_type not in ALLOWED_AVATAR_CONTENT:
        raise HTTPException(415, f"Unsupported image type: {file.content_type}")

    data = await file.read()
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(413, "Avatar exceeds 2 MB")
    if not data:
        raise HTTPException(400, "Empty file")

    ext = EXT_FROM_CONTENT[file.content_type]
    path = storage.save_avatar(character_id, data, ext)
    rel = f"avatars/{path.name}"

    character.avatar_path = rel
    storage.save_character(character)
    return AvatarResult(avatar_path=rel)


@router.delete("/characters/{character_id}/avatar", response_model=AvatarResult)
def delete_avatar_route(character_id: str) -> AvatarResult:
    character = storage.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")
    storage.delete_avatar(character_id)
    character.avatar_path = None
    storage.save_character(character)
    return AvatarResult(avatar_path="")


# ── SillyTavern import / export ───────────────────────────────────────────────

class ImportPayload(BaseModel):
    """Body shape for the import endpoint: a raw v2 card or a wrapper containing one."""
    model_config = ConfigDict(extra="allow")

    spec: Optional[str] = None
    spec_version: Optional[str] = None
    data: Optional[dict[str, Any]] = None


@router.post("/characters/import", response_model=Character)
async def import_character_route(
    payload: ImportPayload, background_tasks: BackgroundTasks
) -> Character:
    try:
        character = silly_tavern.import_silly_tavern_v2(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    summarizer.stamp_character_hash(character)
    storage.save_character(character)
    background_tasks.add_task(summarizer.regen_character_if_stale, character.id)
    logger.info(
        "Imported SillyTavern character (id=%s, name=%s, examples=%d)",
        character.id,
        character.name,
        len(character.response_examples),
    )
    return character


@router.get("/characters/{character_id}/export")
def export_character_route(character_id: str) -> dict[str, Any]:
    character = storage.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")
    return silly_tavern.export_silly_tavern_v2(character)
