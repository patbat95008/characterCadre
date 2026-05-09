"""
Stage 3 storage layer: per-entity JSON repositories with atomic writes.

Layout under DATA_DIR (default: backend/data/):
    characters/<id>.json
    scenarios/<id>.json
    saves/<id>.json
    avatars/<character_id>.<ext>

Every write goes through atomic_write_json (.tmp + rename) so a process kill
mid-write leaves the original file intact.

Reads hit the disk every time — no in-memory cache. The data set is small and
the simplicity is worth more than the marginal IO cost.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from app.models import Character, Save, Scenario

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
CHARACTERS_DIR = DATA_DIR / "characters"
SCENARIOS_DIR = DATA_DIR / "scenarios"
SAVES_DIR = DATA_DIR / "saves"
AVATARS_DIR = DATA_DIR / "avatars"


# ── Path helpers ──────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    for d in (CHARACTERS_DIR, SCENARIOS_DIR, SAVES_DIR, AVATARS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: str) -> None:
    """Write payload to path via .tmp + rename so a mid-write crash is safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _list_ids(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read %s: %s", path, exc)
        return None


# ── Characters ────────────────────────────────────────────────────────────────

def list_characters() -> list[Character]:
    out: list[Character] = []
    for cid in _list_ids(CHARACTERS_DIR):
        c = get_character(cid)
        if c is not None:
            out.append(c)
    return out


def get_character(character_id: str) -> Optional[Character]:
    raw = _read_json(CHARACTERS_DIR / f"{character_id}.json")
    if raw is None:
        return None
    try:
        return Character.model_validate(raw)
    except ValidationError as exc:
        logger.error("Character %s failed validation: %s", character_id, exc)
        return None


def save_character(character: Character) -> None:
    atomic_write_json(
        CHARACTERS_DIR / f"{character.id}.json",
        character.model_dump_json(indent=2),
    )
    logger.info("Persisted character (id=%s, name=%s)", character.id, character.name)


def delete_character(character_id: str) -> bool:
    path = CHARACTERS_DIR / f"{character_id}.json"
    if not path.exists():
        return False
    path.unlink()
    delete_avatar(character_id)
    logger.info("Deleted character (id=%s)", character_id)
    return True


# ── Scenarios ─────────────────────────────────────────────────────────────────

def list_scenarios() -> list[Scenario]:
    out: list[Scenario] = []
    for sid in _list_ids(SCENARIOS_DIR):
        s = get_scenario(sid)
        if s is not None:
            out.append(s)
    return out


def get_scenario(scenario_id: str) -> Optional[Scenario]:
    raw = _read_json(SCENARIOS_DIR / f"{scenario_id}.json")
    if raw is None:
        return None
    try:
        return Scenario.model_validate(raw)
    except ValidationError as exc:
        logger.error("Scenario %s failed validation: %s", scenario_id, exc)
        return None


def save_scenario(scenario: Scenario) -> None:
    atomic_write_json(
        SCENARIOS_DIR / f"{scenario.id}.json",
        scenario.model_dump_json(indent=2),
    )
    logger.info(
        "Persisted scenario (id=%s, name=%s, beats=%d)",
        scenario.id,
        scenario.name,
        len(scenario.beats),
    )


def delete_scenario(scenario_id: str) -> bool:
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        return False
    path.unlink()
    logger.info("Deleted scenario (id=%s)", scenario_id)
    return True


# ── Saves ─────────────────────────────────────────────────────────────────────

def list_saves() -> list[Save]:
    out: list[Save] = []
    for sid in _list_ids(SAVES_DIR):
        s = get_save(sid)
        if s is not None:
            out.append(s)
    return out


def get_save(save_id: str) -> Optional[Save]:
    raw = _read_json(SAVES_DIR / f"{save_id}.json")
    if raw is None:
        return None
    try:
        return Save.model_validate(raw)
    except ValidationError as exc:
        logger.error("Save %s failed validation: %s", save_id, exc)
        return None


def save_save(save: Save) -> None:
    save.updated_at = datetime.now(timezone.utc).isoformat()
    atomic_write_json(
        SAVES_DIR / f"{save.id}.json",
        save.model_dump_json(indent=2),
    )
    logger.info(
        "Persisted save (id=%s, messages=%d, beat=%s)",
        save.id,
        len(save.messages),
        save.current_beat_id or "none",
    )


def delete_save(save_id: str) -> bool:
    path = SAVES_DIR / f"{save_id}.json"
    if not path.exists():
        return False
    path.unlink()
    logger.info("Deleted save (id=%s)", save_id)
    return True


def is_character_in_use(character_id: str) -> bool:
    """True if any save lists this character in active_character_ids."""
    for save in list_saves():
        if character_id in save.active_character_ids:
            return True
    return False


def is_scenario_in_use(scenario_id: str) -> bool:
    """True if any save references this scenario."""
    for save in list_saves():
        if save.scenario_id == scenario_id:
            return True
    return False


# ── Avatars ───────────────────────────────────────────────────────────────────

ALLOWED_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def avatar_path_for(character_id: str) -> Optional[Path]:
    """Return the on-disk avatar path for a character, or None if none exists."""
    for ext in ALLOWED_AVATAR_EXTS:
        p = AVATARS_DIR / f"{character_id}{ext}"
        if p.exists():
            return p
    return None


def save_avatar(character_id: str, file_bytes: bytes, ext: str) -> Path:
    """
    Write avatar bytes to data/avatars/<character_id>.<ext>, removing any prior
    avatar for the character (so we don't accumulate dead files).
    Returns the on-disk path.
    """
    ext = ext.lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in ALLOWED_AVATAR_EXTS:
        raise ValueError(f"Unsupported avatar extension: {ext}")

    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    delete_avatar(character_id)
    path = AVATARS_DIR / f"{character_id}{ext}"
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(file_bytes)
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    logger.info("Saved avatar (character=%s, path=%s)", character_id, path.name)
    return path


def delete_avatar(character_id: str) -> bool:
    """Delete any avatar files for the character. Returns True if at least one was removed."""
    removed = False
    for ext in ALLOWED_AVATAR_EXTS:
        p = AVATARS_DIR / f"{character_id}{ext}"
        if p.exists():
            try:
                p.unlink()
                removed = True
            except OSError as exc:
                logger.warning("Failed to delete avatar %s: %s", p, exc)
    return removed


# ── Bulk reset (test helper) ──────────────────────────────────────────────────

def wipe_data_dir() -> None:
    """Delete the entire data directory tree. Tests only — never called at runtime."""
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    _ensure_dirs()
