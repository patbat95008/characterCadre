"""
Shared fixtures.

`isolated_data_dir` and `client` were copy-pasted across integration modules;
they live here now so the playtest harness can reuse the same seams. They are
deliberately NOT autouse at this level — unit tests like test_seed.py depend on
starting from an genuinely empty data dir. Directories opt in via their own
conftest.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import storage
from app.fixtures import BRAM, NARRATOR, SILVAINE, SCENARIO


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch):
    """Point storage at a temp dir and seed the Ironroot fixtures into it.

    Every *_DIR is patched individually: they are computed at import time from
    DATA_DIR, so patching DATA_DIR alone leaves the others pointing at the real
    backend/data.
    """
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CHARACTERS_DIR", tmp_path / "characters")
    monkeypatch.setattr(storage, "SCENARIOS_DIR", tmp_path / "scenarios")
    monkeypatch.setattr(storage, "SAVES_DIR", tmp_path / "saves")
    monkeypatch.setattr(storage, "AVATARS_DIR", tmp_path / "avatars")
    storage._ensure_dirs()
    storage.save_character(NARRATOR)
    storage.save_character(BRAM)
    storage.save_character(SILVAINE)
    storage.save_scenario(SCENARIO)
    yield tmp_path


@pytest.fixture
def mock_summarizer():
    """Replace the summarizer's structured_chat so background tasks don't hang."""
    fake = AsyncMock(return_value={"summary": "mocked summary"})
    with patch("app.summarizer.structured_chat", fake):
        yield fake


@pytest.fixture
async def client():
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
