"""
Seed an existing save with many messages to exercise token truncation.

Run from the backend/ directory with the venv active:

    python scripts/seed_long_history.py [--save-id <id>] [--pairs N] [--tight]

If --save-id is omitted, the most recently updated save is used.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage
from app.fixtures import BRAM, SCENARIO
from app.models import Message, Save
from app.prompt_builder import _count_messages_tokens, build_character_prompt

N_PAIRS = 40


def _resolve_save(save_id: str | None) -> Save:
    if save_id:
        save = storage.get_save(save_id)
        if save is None:
            raise SystemExit(f"Save {save_id!r} not found")
        return save
    saves = storage.list_saves()
    if not saves:
        raise SystemExit("No saves found — start the backend once to seed the library.")
    saves.sort(key=lambda s: s.updated_at, reverse=True)
    return saves[0]


def seed(save_id: str | None = None, n_pairs: int = N_PAIRS, demo_budget: int | None = None) -> None:
    save = _resolve_save(save_id)
    scenario = storage.get_scenario(save.scenario_id) or SCENARIO

    print(f"Save loaded: {save.id} ({save.name})")
    print(f"Messages before seeding: {len(save.messages)}")

    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for i in range(n_pairs):
        save.messages.append(Message(
            id=str(uuid.uuid4()),
            role="user",
            content=(
                f"Turn {i}: I carefully examine the passage ahead, "
                f"looking for traps and listening for sounds from the darkness beyond."
            ),
            timestamp=now,
        ))
        save.messages.append(Message(
            id=str(uuid.uuid4()),
            role="character",
            character_id=BRAM.id,
            content=(
                f"Turn {i}: Nothing obvious, lad. "
                f"But keep your eyes low — that's where the old dwarven traps sit. "
                f"Move slow."
            ),
            timestamp=now,
        ))
        added += 2

    storage.save_save(save)
    print(f"Messages after seeding:  {len(save.messages)} (+{added})")

    if demo_budget:
        save.max_context_tokens = demo_budget
    prompt_messages = build_character_prompt(BRAM, scenario, save, save.user_name)
    chat_in_prompt = [m for m in prompt_messages if m["role"] != "system"]
    chat_tokens = _count_messages_tokens(chat_in_prompt)

    print()
    print("-- Truncation report -----------------------------------------")
    print(f"  Save has {len(save.messages)} messages total")
    print(f"  Prompt includes {len(chat_in_prompt)} chat messages after truncation")
    print(f"  Chat tokens used: {chat_tokens}")
    print(f"  max_context_tokens: {save.max_context_tokens}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Seed a save with bulk history.")
    parser.add_argument("--save-id", type=str, default=None)
    parser.add_argument("--pairs", type=int, default=N_PAIRS)
    parser.add_argument("--tight", action="store_true",
                        help="Use a 2048-token budget to force visible truncation.")
    args = parser.parse_args()
    seed(
        save_id=args.save_id,
        n_pairs=args.pairs,
        demo_budget=2048 if args.tight else None,
    )
