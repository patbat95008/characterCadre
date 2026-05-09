"""
Defaults shared across the app: the canonical RP system prompt prefilled when
authors create a fresh scenario, and a couple of small helpers.
"""
from __future__ import annotations

DEFAULT_SCENARIO_SYSTEM_PROMPT = (
    "This is a collaborative roleplay adventure. "
    "You are playing an in-character role in an ongoing story. "
    "Stay in character at all times. "
    "Do not break the fourth wall or acknowledge that this is a game unless {{user}} does first. "
    "{{user}} is the player character — do not speak for them or make decisions on their behalf. "
    "Keep responses immersive and concise."
)
