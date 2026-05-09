"""
First-run seeding of the data directory.

When the backend starts, if `data/characters/` and `data/scenarios/` are both
empty, the hardcoded fixtures (Narrator, Bram, Ironroot Dungeon) are written
to disk as the initial library. They are full citizens after that — the user
can edit, duplicate, or delete them.

A legacy Stage 2 save lives at `data/saves/stage1.json` with a UUID-based
internal id. We migrate it into the new "<save.id>.json" layout so the existing
playthrough survives the upgrade.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app import storage
from app.fixtures import BRAM, NARRATOR, SILVAINE, SCENARIO, make_stage1_save
from app.models import Save

logger = logging.getLogger(__name__)


def _seed_library_if_empty() -> bool:
    """Write fixtures to disk if both characters and scenarios dirs are empty.

    Returns True if seeding ran, False if the dirs already had content.
    """
    if storage.list_characters() or storage.list_scenarios():
        return False

    storage.save_character(NARRATOR)
    storage.save_character(BRAM)
    storage.save_character(SILVAINE)
    storage.save_scenario(SCENARIO)
    logger.info(
        "seeded fixture library (characters=3, scenarios=1, scenario=%s)",
        SCENARIO.id,
    )
    return True


def _migrate_legacy_stage1_save() -> bool:
    """Rename data/saves/stage1.json to data/saves/<save.id>.json if needed.

    The Stage 2 storage layer wrote a single save file at a fixed name regardless
    of its internal id. Stage 3 uses one file per save, keyed by id.
    Returns True if a migration happened.
    """
    legacy: Path = storage.SAVES_DIR / "stage1.json"
    if not legacy.exists():
        return False

    try:
        save = Save.model_validate_json(legacy.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Legacy stage1.json failed to parse, skipping migration: %s", exc)
        return False

    target = storage.SAVES_DIR / f"{save.id}.json"
    if target.exists() and target.resolve() != legacy.resolve():
        logger.info(
            "Legacy stage1.json found but %s already exists; deleting legacy file", target.name
        )
        legacy.unlink()
        return False

    if target.resolve() == legacy.resolve():
        # save.id literally is "stage1" — nothing to migrate.
        return False

    storage.save_save(save)
    legacy.unlink()
    logger.info(
        "Migrated legacy save data/saves/stage1.json -> data/saves/%s.json", save.id
    )
    return True


def _seed_default_save_if_empty() -> bool:
    """Create one example save against the seeded scenario if no saves exist."""
    if storage.list_saves():
        return False
    save = make_stage1_save()
    storage.save_save(save)
    logger.info(
        "Seeded default save (id=%s, scenario=%s)", save.id, save.scenario_id
    )
    return True


def run_if_empty() -> None:
    """Idempotent startup hook. Seeds the library + creates a starter save."""
    storage._ensure_dirs()  # noqa: SLF001 — internal helper, fine in seed boot
    seeded_lib = _seed_library_if_empty()
    _migrate_legacy_stage1_save()
    if seeded_lib:
        _seed_default_save_if_empty()
