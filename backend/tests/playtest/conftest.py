"""
Live-playtest fixtures.

These tests run the real app in-process (ASGITransport) with *no* mocks on
`app.phases`, so every turn goes to the real local LLM. Data lives in a temp
dir seeded from the Ironroot fixtures — a playtest never touches backend/data.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest

from app.markers import MARKER_LOGGER_NAME
from app.ollama_client import OLLAMA_MODEL
from tests.playtest.harness import MarkerSink, PlaytestRunner


@pytest.fixture(autouse=True)
def _isolate(isolated_data_dir):
    """Every playtest gets its own seeded data dir."""
    yield isolated_data_dir


@pytest.fixture
def marker_sink():
    """Capture markers emitted in-process for the duration of a test."""
    sink = MarkerSink()
    logger = logging.getLogger(MARKER_LOGGER_NAME)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(sink)
    try:
        yield sink
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous_level)


@pytest.fixture
def runner(client, marker_sink) -> PlaytestRunner:
    return PlaytestRunner(client, marker_sink)


@pytest.fixture
async def ollama_ready():
    """Skip live tests unless the model is actually resident.

    `/api/health/ollama` checks `ps()`, not just that the port is open, so this
    distinguishes "Ollama is down" from "Ollama is up but the model is cold" —
    the latter would otherwise show up as an opaque 30s structured-call timeout.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            r = await ac.get("/api/health/ollama")
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Ollama health check raised: {exc}")
        if r.status_code != 200:
            pytest.skip(
                f"Ollama not ready for live playtests: {r.status_code} {r.text}. "
                f"Start it and warm `{OLLAMA_MODEL}` (launch.py does both)."
            )
    yield


@pytest.fixture
def copy_debug_logs():
    """If CC_DEBUG=1, copy the prompt/response dumps into the run directory.

    They are truncated at process start and correlate to turns via the turn=
    tag in each entry header, so they are only useful alongside the run they
    came from.
    """
    def copy_into(run_dir: Path) -> None:
        if os.environ.get("CC_DEBUG") != "1":
            return
        logs_dir = Path(os.environ.get("CC_LOGS_DIR", "logs"))
        for name in ("ollama-input.log", "ollama-output.log"):
            src = logs_dir / name
            if src.exists():
                shutil.copy2(src, run_dir / name)
    return copy_into
