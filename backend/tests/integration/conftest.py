"""Integration tests always run against an isolated, pre-seeded data dir."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate(isolated_data_dir):
    yield isolated_data_dir
