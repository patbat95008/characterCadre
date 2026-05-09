"""Unit tests for summarizer.py — hash determinism and Ollama-failure fallback."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app import storage, summarizer
from app.fixtures import BRAM
from app.ollama_client import OllamaUnreachableError


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CHARACTERS_DIR", tmp_path / "characters")
    monkeypatch.setattr(storage, "SCENARIOS_DIR", tmp_path / "scenarios")
    monkeypatch.setattr(storage, "SAVES_DIR", tmp_path / "saves")
    monkeypatch.setattr(storage, "AVATARS_DIR", tmp_path / "avatars")
    storage._ensure_dirs()
    yield


# ── Hash determinism ──────────────────────────────────────────────────────────

def test_character_hash_is_deterministic():
    h1 = summarizer.character_description_hash("hello world")
    h2 = summarizer.character_description_hash("hello world")
    assert h1 == h2
    assert len(h1) == 16


def test_character_hash_changes_with_description():
    a = summarizer.character_description_hash("hello world")
    b = summarizer.character_description_hash("hello WORLD")
    assert a != b


def test_scenario_hash_changes_with_either_input():
    base = summarizer.scenario_summary_hash("intro", "system")
    assert summarizer.scenario_summary_hash("INTRO", "system") != base
    assert summarizer.scenario_summary_hash("intro", "SYSTEM") != base


def test_beat_hash_changes_with_each_field():
    base = summarizer.beat_summary_hash("name", "desc", "cond")
    assert summarizer.beat_summary_hash("NAME", "desc", "cond") != base
    assert summarizer.beat_summary_hash("name", "DESC", "cond") != base
    assert summarizer.beat_summary_hash("name", "desc", "COND") != base


# ── Ollama failure → empty summary ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_character_summary_returns_empty_on_unreachable():
    with patch(
        "app.summarizer.structured_chat",
        new=AsyncMock(side_effect=OllamaUnreachableError("boom")),
    ):
        result = await summarizer.generate_character_summary("Bram", "A dwarf.")
    assert result == ""


@pytest.mark.asyncio
async def test_generate_character_summary_strips_quotes():
    fake = AsyncMock(return_value={"summary": '"a dwarven fighter"'})
    with patch("app.summarizer.structured_chat", fake):
        result = await summarizer.generate_character_summary("Bram", "A dwarf.")
    assert result == "a dwarven fighter"


# ── Stamping ──────────────────────────────────────────────────────────────────

def test_stamp_character_hash_sets_hash_to_match_description():
    char = BRAM.model_copy()
    char.description_hash = ""
    summarizer.stamp_character_hash(char)
    assert char.description_hash == summarizer.character_description_hash(char.description)


# ── Stale-detection: regen_character_if_stale ─────────────────────────────────

@pytest.mark.asyncio
async def test_regen_character_if_stale_skips_when_hash_current():
    char = BRAM.model_copy()
    summarizer.stamp_character_hash(char)
    char.description_summary = "already up to date"
    storage.save_character(char)

    fake = AsyncMock(return_value={"summary": "should not be called"})
    with patch("app.summarizer.structured_chat", fake):
        await summarizer.regen_character_if_stale(char.id)

    fake.assert_not_called()
    fetched = storage.get_character(char.id)
    assert fetched.description_summary == "already up to date"


@pytest.mark.asyncio
async def test_regen_character_if_stale_updates_when_description_changed():
    char = BRAM.model_copy()
    char.description_hash = "stale"
    char.description_summary = "old"
    storage.save_character(char)

    fake = AsyncMock(return_value={"summary": "new summary"})
    with patch("app.summarizer.structured_chat", fake):
        await summarizer.regen_character_if_stale(char.id)

    fake.assert_called()
    fetched = storage.get_character(char.id)
    assert fetched.description_summary == "new summary"
    assert fetched.description_hash == summarizer.character_description_hash(char.description)
