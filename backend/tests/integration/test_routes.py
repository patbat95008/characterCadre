"""
Integration tests for the Stage 3 CRUD routes.

These hit the FastAPI app via httpx.AsyncClient with an isolated DATA_DIR per
test, so concurrent runs don't tread on each other. Ollama is mocked at the
summarizer boundary so background regen tasks don't try to talk to a real
server.
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import storage
from app.fixtures import BRAM, NARRATOR, SCENARIO


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CHARACTERS_DIR", tmp_path / "characters")
    monkeypatch.setattr(storage, "SCENARIOS_DIR", tmp_path / "scenarios")
    monkeypatch.setattr(storage, "SAVES_DIR", tmp_path / "saves")
    monkeypatch.setattr(storage, "AVATARS_DIR", tmp_path / "avatars")
    storage._ensure_dirs()
    storage.save_character(NARRATOR)
    storage.save_character(BRAM)
    storage.save_scenario(SCENARIO)
    yield


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


# ── Characters ────────────────────────────────────────────────────────────────

async def test_list_characters_returns_seeded(client, mock_summarizer):
    r = await client.get("/api/characters")
    assert r.status_code == 200
    rows = r.json()
    ids = {row["id"] for row in rows}
    assert {NARRATOR.id, BRAM.id} <= ids


async def test_get_character_returns_full(client, mock_summarizer):
    r = await client.get(f"/api/characters/{BRAM.id}")
    assert r.status_code == 200
    assert r.json()["name"] == BRAM.name


async def test_get_missing_character_returns_404(client, mock_summarizer):
    r = await client.get("/api/characters/ghost")
    assert r.status_code == 404


async def test_create_character_then_get(client, mock_summarizer):
    payload = {
        "id": "",
        "name": "Test Character",
        "description": "A test.",
        "is_dm": False,
        "response_examples": [],
    }
    r = await client.post("/api/characters", json=payload)
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert new_id  # auto-assigned

    r2 = await client.get(f"/api/characters/{new_id}")
    assert r2.status_code == 200


async def test_update_character_preserves_summary_when_description_unchanged(
    client, mock_summarizer
):
    r = await client.get(f"/api/characters/{BRAM.id}")
    bram = r.json()
    bram["name"] = "Bram Updated"  # description unchanged
    bram["description_summary"] = ""  # should be preserved by route logic
    r2 = await client.put(f"/api/characters/{BRAM.id}", json=bram)
    assert r2.status_code == 200
    assert r2.json()["description_summary"] == BRAM.description_summary


async def test_update_character_with_changed_description_schedules_regen(
    client, mock_summarizer
):
    r = await client.get(f"/api/characters/{BRAM.id}")
    bram = r.json()
    bram["description"] = bram["description"] + " EDITED"
    bram["description_summary"] = ""
    r2 = await client.put(f"/api/characters/{BRAM.id}", json=bram)
    assert r2.status_code == 200
    # Background task should fire — give it a moment via the next request
    # (BackgroundTasks run after the response in TestClient too)
    # We can't easily await the background task, but we can verify the hash updated.
    fetched = storage.get_character(BRAM.id)
    from app import summarizer
    assert fetched.description_hash == summarizer.character_description_hash(fetched.description)


async def test_delete_character(client, mock_summarizer):
    r = await client.delete(f"/api/characters/{BRAM.id}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    r2 = await client.get(f"/api/characters/{BRAM.id}")
    assert r2.status_code == 404


async def test_import_silly_tavern_character(client, mock_summarizer):
    payload = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "Imported",
            "description": "A test imported character.",
            "personality": "Brave.",
            "mes_example": "<START>\n{{user}}: hi\n{{char}}: hello\n",
        },
    }
    r = await client.post("/api/characters/import", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Imported"
    assert "Brave." in body["description"]
    assert body["response_examples"] == [{"user": "hi", "char": "hello"}]


async def test_export_character_returns_v2_card(client, mock_summarizer):
    r = await client.get(f"/api/characters/{BRAM.id}/export")
    assert r.status_code == 200
    body = r.json()
    assert body["spec"] == "chara_card_v2"
    assert body["data"]["name"] == BRAM.name
    assert "description_summary" not in body["data"]


async def test_upload_avatar(client, mock_summarizer):
    files = {"file": ("avatar.png", io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "image/png")}
    r = await client.post(f"/api/characters/{BRAM.id}/avatar", files=files)
    assert r.status_code == 200, r.text
    assert r.json()["avatar_path"].startswith("avatars/")
    char = storage.get_character(BRAM.id)
    assert char.avatar_path is not None


async def test_upload_avatar_rejects_unsupported_type(client, mock_summarizer):
    files = {"file": ("avatar.bmp", io.BytesIO(b"data"), "image/bmp")}
    r = await client.post(f"/api/characters/{BRAM.id}/avatar", files=files)
    assert r.status_code == 415


# ── Scenarios ─────────────────────────────────────────────────────────────────

async def test_list_scenarios(client, mock_summarizer):
    r = await client.get("/api/scenarios")
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()}
    assert SCENARIO.id in ids


async def test_create_scenario_with_default_system_prompt(client, mock_summarizer):
    payload = {
        "id": "",
        "name": "New scenario",
        "initial_message": "Once upon a time.",
        "system_prompt": "",
        "summary": "",
        "summary_hash": "",
        "persistent_messages": [],
        "dm_only_info": [],
        "recommended_character_ids": [],
        "beats": [],
    }
    r = await client.post("/api/scenarios", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    from app.defaults import DEFAULT_SCENARIO_SYSTEM_PROMPT
    assert body["system_prompt"] == DEFAULT_SCENARIO_SYSTEM_PROMPT


async def test_update_scenario_assigns_beat_ids(client, mock_summarizer):
    r = await client.get(f"/api/scenarios/{SCENARIO.id}")
    scenario = r.json()
    scenario["beats"] = [
        {
            "id": "",
            "order": 0,
            "name": "Town",
            "description": "Bustling.",
            "summary": "",
            "summary_hash": "",
            "transition_condition": "Player leaves the gates.",
            "starter_prompt": "You stand at the town square.",
        },
        {
            "id": "",
            "order": 1,
            "name": "Forest",
            "description": "Dense.",
            "summary": "",
            "summary_hash": "",
            "transition_condition": "Player reaches the dungeon.",
            "starter_prompt": "You enter the forest.",
        },
    ]
    r2 = await client.put(f"/api/scenarios/{SCENARIO.id}", json=scenario)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert len(body["beats"]) == 2
    assert all(b["id"] for b in body["beats"])
    assert body["beats"][0]["order"] == 0
    assert body["beats"][1]["order"] == 1


# ── Saves ─────────────────────────────────────────────────────────────────────

async def test_create_save_for_beatless_scenario_uses_initial_message(client, mock_summarizer):
    r = await client.post(
        "/api/saves",
        json={
            "scenario_id": SCENARIO.id,
            "active_character_ids": [NARRATOR.id, BRAM.id],
            "user_name": "Alice",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_beat_id"] is None
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"] == SCENARIO.initial_message


async def test_create_save_with_beats_still_uses_initial_message(client, mock_summarizer):
    # Beats are additive — the initial_message must always open the adventure
    r = await client.get(f"/api/scenarios/{SCENARIO.id}")
    scenario = r.json()
    scenario["beats"] = [{
        "id": "beat-a", "order": 0, "name": "Opener", "description": "x",
        "summary": "", "summary_hash": "",
        "transition_condition": "y", "starter_prompt": "Welcome to the opener!",
    }]
    await client.put(f"/api/scenarios/{SCENARIO.id}", json=scenario)

    r = await client.post(
        "/api/saves",
        json={
            "scenario_id": SCENARIO.id,
            "active_character_ids": [NARRATOR.id, BRAM.id],
            "user_name": "Alice",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["current_beat_id"] is None
    assert body["messages"][0]["content"] == SCENARIO.initial_message


async def test_create_save_rejects_zero_dms(client, mock_summarizer):
    r = await client.post(
        "/api/saves",
        json={
            "scenario_id": SCENARIO.id,
            "active_character_ids": [BRAM.id],
            "user_name": "Alice",
        },
    )
    assert r.status_code == 400


async def test_advance_beat_soft_appends(client, mock_summarizer):
    r = await client.get(f"/api/scenarios/{SCENARIO.id}")
    scenario = r.json()
    scenario["beats"] = [
        {"id": "b1", "order": 0, "name": "B1", "description": "", "summary": "",
         "summary_hash": "", "transition_condition": "", "starter_prompt": "Start b1."},
        {"id": "b2", "order": 1, "name": "B2", "description": "", "summary": "",
         "summary_hash": "", "transition_condition": "", "starter_prompt": "Start b2."},
    ]
    await client.put(f"/api/scenarios/{SCENARIO.id}", json=scenario)
    r = await client.post("/api/saves", json={
        "scenario_id": SCENARIO.id,
        "active_character_ids": [NARRATOR.id, BRAM.id],
        "user_name": "Alice",
    })
    save_id = r.json()["id"]

    r2 = await client.post(
        f"/api/saves/{save_id}/advance-beat",
        json={"next_beat_id": "b2", "wipe_context": False},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["current_beat_id"] == "b2"
    assert len(body["messages"]) == 2
    assert body["messages"][-1]["content"] == "Start b2."


async def test_advance_beat_hard_wipes_messages(client, mock_summarizer):
    r = await client.get(f"/api/scenarios/{SCENARIO.id}")
    scenario = r.json()
    scenario["beats"] = [
        {"id": "b1", "order": 0, "name": "B1", "description": "", "summary": "",
         "summary_hash": "", "transition_condition": "", "starter_prompt": "Start b1."},
        {"id": "b2", "order": 1, "name": "B2", "description": "", "summary": "",
         "summary_hash": "", "transition_condition": "", "starter_prompt": "Start b2."},
    ]
    await client.put(f"/api/scenarios/{SCENARIO.id}", json=scenario)
    r = await client.post("/api/saves", json={
        "scenario_id": SCENARIO.id,
        "active_character_ids": [NARRATOR.id, BRAM.id],
        "user_name": "Alice",
    })
    save_id = r.json()["id"]

    r2 = await client.post(
        f"/api/saves/{save_id}/advance-beat",
        json={"next_beat_id": "b2", "wipe_context": True},
    )
    body = r2.json()
    assert body["current_beat_id"] == "b2"
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"] == "Start b2."
    assert body["user_name"] == "Alice"  # metadata preserved


async def test_advance_beat_rejects_backward(client, mock_summarizer):
    r = await client.get(f"/api/scenarios/{SCENARIO.id}")
    scenario = r.json()
    scenario["beats"] = [
        {"id": "b1", "order": 0, "name": "B1", "description": "", "summary": "",
         "summary_hash": "", "transition_condition": "", "starter_prompt": "Start b1."},
        {"id": "b2", "order": 1, "name": "B2", "description": "", "summary": "",
         "summary_hash": "", "transition_condition": "", "starter_prompt": "Start b2."},
    ]
    await client.put(f"/api/scenarios/{SCENARIO.id}", json=scenario)
    r = await client.post("/api/saves", json={
        "scenario_id": SCENARIO.id,
        "active_character_ids": [NARRATOR.id, BRAM.id],
        "user_name": "Alice",
    })
    save_id = r.json()["id"]

    # Advance to b2
    await client.post(
        f"/api/saves/{save_id}/advance-beat",
        json={"next_beat_id": "b2", "wipe_context": False},
    )
    # Try to go back to b1
    r3 = await client.post(
        f"/api/saves/{save_id}/advance-beat",
        json={"next_beat_id": "b1", "wipe_context": False},
    )
    assert r3.status_code == 400


async def test_sandbox_mode_toggle(client, mock_summarizer):
    r = await client.post("/api/saves", json={
        "scenario_id": SCENARIO.id,
        "active_character_ids": [NARRATOR.id, BRAM.id],
        "user_name": "Alice",
    })
    save_id = r.json()["id"]
    r2 = await client.post(f"/api/saves/{save_id}/sandbox-mode", json={"enabled": True})
    assert r2.status_code == 200
    assert r2.json()["sandbox_mode"] is True


async def test_list_saves_sorted_by_updated_at(client, mock_summarizer):
    a = await client.post("/api/saves", json={
        "scenario_id": SCENARIO.id,
        "active_character_ids": [NARRATOR.id, BRAM.id],
        "user_name": "A",
    })
    b = await client.post("/api/saves", json={
        "scenario_id": SCENARIO.id,
        "active_character_ids": [NARRATOR.id, BRAM.id],
        "user_name": "B",
    })
    r = await client.get("/api/saves")
    rows = r.json()
    # Most recently created should be first
    assert rows[0]["id"] == b.json()["id"]
