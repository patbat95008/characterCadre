"""Unit tests for seed.run_if_empty()."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import seed, storage
from app.fixtures import BRAM, NARRATOR, SCENARIO


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CHARACTERS_DIR", tmp_path / "characters")
    monkeypatch.setattr(storage, "SCENARIOS_DIR", tmp_path / "scenarios")
    monkeypatch.setattr(storage, "SAVES_DIR", tmp_path / "saves")
    monkeypatch.setattr(storage, "AVATARS_DIR", tmp_path / "avatars")
    yield


def test_seed_writes_fixtures_when_empty():
    seed.run_if_empty()
    assert storage.get_character(NARRATOR.id) is not None
    assert storage.get_character(BRAM.id) is not None
    assert storage.get_scenario(SCENARIO.id) is not None
    saves = storage.list_saves()
    assert len(saves) == 1


def test_seed_is_noop_when_library_already_populated():
    storage._ensure_dirs()
    storage.save_character(NARRATOR)
    seed.run_if_empty()
    # Should not have written BRAM or SCENARIO
    assert storage.get_character(BRAM.id) is None
    assert storage.get_scenario(SCENARIO.id) is None


def test_seed_migrates_legacy_stage1_save():
    storage._ensure_dirs()
    storage.save_character(NARRATOR)
    storage.save_character(BRAM)
    storage.save_scenario(SCENARIO)
    # Drop a legacy stage1.json with a UUID-based save id
    from app.fixtures import make_stage1_save
    legacy_save = make_stage1_save()
    legacy_path = storage.SAVES_DIR / "stage1.json"
    legacy_path.write_text(legacy_save.model_dump_json(indent=2), encoding="utf-8")

    seed.run_if_empty()

    # Legacy file should be gone, save accessible by its id
    assert not legacy_path.exists()
    assert storage.get_save(legacy_save.id) is not None
