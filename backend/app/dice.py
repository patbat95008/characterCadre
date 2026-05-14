"""
Dice roll mechanics for CharacterCadre skill checks.

Supports D20 and D100 rolls with Easy / Medium / Hard difficulty thresholds.
Natural crits (rolling the minimum or maximum value) override the threshold:
  - Nat min (roll == 1)   → critical_failure  (always fails; must be comical)
  - Nat max (roll == max) → critical_success   (always succeeds; improbably triumphant)
"""
from __future__ import annotations

import random
from typing import Literal

from pydantic import BaseModel, ConfigDict

# ── Constants ─────────────────────────────────────────────────────────────────

DICE_SIDES: dict[str, int] = {
    "D20": 20,
    "D100": 100,
}

DIFFICULTY_THRESHOLDS: dict[str, dict[str, int]] = {
    "D20":  {"Easy": 5,  "Medium": 10, "Hard": 15},
    "D100": {"Easy": 25, "Medium": 50, "Hard": 75},
}

DiceType = Literal["D20", "D100"]
Difficulty = Literal["Easy", "Medium", "Hard"]
Outcome = Literal["critical_failure", "failure", "success", "critical_success"]

# ── Plain-English strings injected into the LLM context ───────────────────────

OUTCOME_LLM_TEXT: dict[str, str] = {
    "critical_failure": (
        "CRITICAL FAILURE — The player rolled the minimum possible value (a natural 1). "
        "This is an automatic failure regardless of difficulty. "
        "The consequence must be dramatic and somewhat comical."
    ),
    "failure": "FAILURE — The skill check was not successful. The attempt does not succeed.",
    "success": "SUCCESS — The skill check passed. The attempt succeeds.",
    "critical_success": (
        "CRITICAL SUCCESS — The player rolled the maximum possible value. "
        "This is an automatic success regardless of difficulty. "
        "The consequence should feel triumphant or improbably fortunate."
    ),
}

# Short labels for display in prompts and logs
OUTCOME_SHORT: dict[str, str] = {
    "critical_failure": "Critical Failure",
    "failure": "Failure",
    "success": "Success",
    "critical_success": "Critical Success",
}


# ── Data model ────────────────────────────────────────────────────────────────

class DiceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dice: str           # "D20" or "D100"
    value: int          # rolled value
    max_value: int      # maximum possible value (sides)
    threshold: int      # minimum value needed for success
    difficulty: str     # "Easy" | "Medium" | "Hard"
    outcome: str        # "critical_failure" | "failure" | "success" | "critical_success"
    is_nat_crit: bool   # True when value == 1 or value == max_value


# ── Core functions ────────────────────────────────────────────────────────────

def roll_dice(dice: str, difficulty: str) -> DiceResult:
    """
    Roll the specified die, apply the difficulty threshold, and return a DiceResult.
    Natural min (1) → critical_failure; natural max → critical_success.
    """
    sides = DICE_SIDES[dice]
    threshold = DIFFICULTY_THRESHOLDS[dice][difficulty]
    value = random.randint(1, sides)

    is_nat_min = value == 1
    is_nat_max = value == sides

    if is_nat_min:
        outcome: Outcome = "critical_failure"
    elif is_nat_max:
        outcome = "critical_success"
    elif value >= threshold:
        outcome = "success"
    else:
        outcome = "failure"

    return DiceResult(
        dice=dice,
        value=value,
        max_value=sides,
        threshold=threshold,
        difficulty=difficulty,
        outcome=outcome,
        is_nat_crit=is_nat_min or is_nat_max,
    )


def outcome_is_success(outcome: str) -> bool:
    """Return True for outcomes that count as a pass (success or critical_success)."""
    return outcome in ("success", "critical_success")


def build_llm_context_message(action_text: str, result: DiceResult) -> str:
    """
    Build the is_dm_only context message injected into save.messages so the
    Director and DM know the roll outcome when narrating the turn.
    """
    return (
        f"[Skill Check: {result.dice} ({result.difficulty} difficulty) "
        f'for "{action_text}" — '
        f"Rolled {result.value} out of {result.max_value} "
        f"(threshold for success: {result.threshold}). "
        f"{OUTCOME_LLM_TEXT[result.outcome]}]"
    )
