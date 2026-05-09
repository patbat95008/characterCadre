"""Unit tests for storage.py — per-entity CRUD with atomic writes."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import storage
from app.fixtures import BRAM, NARRATOR, SCENARIO, make_stage1_save


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CHARACTERS_DIR", tmp_path / "characters")
    monkeypatch.setattr(storage, "SCENARIOS_DIR", tmp_path / "scenarios")
    monkeypatch.setattr(storage, "SAVES_DIR", tmp_path / "saves")
    monkeypatch.setattr(storage, "AVATARS_DIR", tmp_path / "avatars")
    storage._ensure_dirs()
    yield


# ── Atomic write ──────────────────────────────────────────────────────────────

def test_atomic_write_creates_file(tmp_path: Path):
    path = tmp_path / "scenarios" / "x.json"
    storage.atomic_write_json(path, '{"a": 1}')
    assert path.read_text() == '{"a": 1}'
    # No leftover .tmp
    assert not (tmp_path / "scenarios" / "x.json.tmp").exists()


def test_atomic_write_replaces_existing(tmp_path: Path):
    path = tmp_path / "scenarios" / "x.json"
    storage.atomic_write_json(path, '{"v": "old"}')
    storage.atomic_write_json(path, '{"v": "new"}')
    assert path.read_text() == '{"v": "new"}'


def test_atomic_write_failure_leaves_original_intact(tmp_path: Path, monkeypatch):
    """If os.replace fails, the original file must remain unchanged."""
    path = tmp_path / "scenarios" / "x.json"
    storage.atomic_write_json(path, '{"v": "original"}')

    def fake_replace(*args, **kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", fake_replace)
    with pytest.raises(OSError):
        storage.atomic_write_json(path, '{"v": "new"}')
    assert path.read_text() == '{"v": "original"}'


# ── Character CRUD ────────────────────────────────────────────────────────────

def test_character_round_trip():
    storage.save_character(BRAM)
    fetched = storage.get_character(BRAM.id)
    assert fetched is not None
    assert fetched.name == BRAM.name
    assert fetched.description == BRAM.description
    assert len(fetched.response_examples) == len(BRAM.response_examples)


def test_list_characters_returns_all_persisted():
    storage.save_character(NARRATOR)
    storage.save_character(BRAM)
    rows = storage.list_characters()
    ids = {c.id for c in rows}
    assert ids == {NARRATOR.id, BRAM.id}


def test_delete_character_returns_true_for_existing():
    storage.save_character(BRAM)
    assert storage.delete_character(BRAM.id) is True
    assert storage.get_character(BRAM.id) is None


def test_delete_character_returns_false_for_missing():
    assert storage.delete_character("does-not-exist") is False


def test_get_character_returns_none_for_missing():
    assert storage.get_character("ghost") is None


# ── Scenario CRUD ─────────────────────────────────────────────────────────────

def test_scenario_round_trip():
    storage.save_scenario(SCENARIO)
    fetched = storage.get_scenario(SCENARIO.id)
    assert fetched is not None
    assert fetched.name == SCENARIO.name
    assert fetched.beats == SCENARIO.beats


def test_delete_scenario():
    storage.save_scenario(SCENARIO)
    assert storage.delete_scenario(SCENARIO.id) is True
    assert storage.get_scenario(SCENARIO.id) is None


# ── Save CRUD ─────────────────────────────────────────────────────────────────

def test_save_round_trip():
    save = make_stage1_save()
    storage.save_save(save)
    fetched = storage.get_save(save.id)
    assert fetched is not None
    assert fetched.id == save.id
    assert len(fetched.messages) == len(save.messages)


def test_save_save_updates_updated_at():
    save = make_stage1_save()
    original_updated = save.updated_at
    storage.save_save(save)
    fetched = storage.get_save(save.id)
    assert fetched is not None
    assert fetched.updated_at >= original_updated


def test_list_saves_returns_all():
    s1 = make_stage1_save()
    s2 = make_stage1_save()
    storage.save_save(s1)
    storage.save_save(s2)
    ids = {s.id for s in storage.list_saves()}
    assert s1.id in ids and s2.id in ids


def test_is_character_in_use():
    save = make_stage1_save()
    storage.save_save(save)
    assert storage.is_character_in_use(BRAM.id) is True
    assert storage.is_character_in_use("unused-character") is False


def test_is_scenario_in_use():
    save = make_stage1_save()
    storage.save_save(save)
    assert storage.is_scenario_in_use(SCENARIO.id) is True
    assert storage.is_scenario_in_use("unused-scenario") is False


# ── Avatar handling ───────────────────────────────────────────────────────────

def test_avatar_round_trip():
    path = storage.save_avatar(BRAM.id, b"fake-image-bytes", ".png")
    assert path.exists()
    assert path.read_bytes() == b"fake-image-bytes"
    found = storage.avatar_path_for(BRAM.id)
    assert found is not None and found.suffix == ".png"


def test_save_avatar_replaces_old_extension():
    storage.save_avatar(BRAM.id, b"png-bytes", ".png")
    storage.save_avatar(BRAM.id, b"jpg-bytes", ".jpg")
    # The .png should have been removed
    assert not (storage.AVATARS_DIR / f"{BRAM.id}.png").exists()
    assert (storage.AVATARS_DIR / f"{BRAM.id}.jpg").exists()


def test_save_avatar_rejects_unsupported_ext():
    with pytest.raises(ValueError):
        storage.save_avatar(BRAM.id, b"data", ".bmp")


def test_delete_avatar_when_present():
    storage.save_avatar(BRAM.id, b"data", ".png")
    assert storage.delete_avatar(BRAM.id) is True
    assert storage.avatar_path_for(BRAM.id) is None


def test_delete_character_also_removes_avatar():
    storage.save_character(BRAM)
    storage.save_avatar(BRAM.id, b"data", ".png")
    storage.delete_character(BRAM.id)
    assert storage.avatar_path_for(BRAM.id) is None
