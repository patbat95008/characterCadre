"""
Live playthroughs against the real local LLM.

Excluded from the default run by `addopts = -m 'not live'`. To run them:

    python -m pytest tests/playtest -m live -s

Each test drives one script, writes its artifacts to tests/playtest/runs/, and
fails only on structural checks. Prose is never asserted — read transcript.md.

A full playthrough is roughly 7 Ollama calls per turn, so budget minutes per
test. The `-s` is worth passing: the run directory is printed as it finishes.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import storage
from app.ollama_client import OLLAMA_MODEL
from tests.playtest.harness import PlaytestRunner
from tests.playtest.model import PlaytestScript, Step
from tests.playtest.report import new_run_dir, write_run
from tests.playtest.scripts import (
    ALWAYS_FIRST,
    DUTIFUL,
    RANDOM_CHOICE,
    TAVERN_DERAIL,
    TRAP_AND_SURRENDER,
    baseline_probes,
)
from tests.playtest import checks as C

pytestmark = pytest.mark.live


async def _play(runner: PlaytestRunner, script: PlaytestScript, copy_debug_logs) -> None:
    started = datetime.now(timezone.utc)
    turns = await runner.run_script(script)
    duration = (datetime.now(timezone.utc) - started).total_seconds()

    meta = {
        "model": OLLAMA_MODEL,
        "started": started.isoformat(),
        "save_id": runner.save_id,
        "duration_s": duration,
        "user_name": runner.user_name,
    }

    save = storage.get_save(runner.save_id)
    run_dir = new_run_dir(script.name)
    write_run(
        run_dir, script, turns, meta,
        save_json=save.model_dump_json(indent=2) if save else None,
    )
    copy_debug_logs(run_dir)

    # Deliberately ASCII: with -s these go straight to the console, which is
    # cp1252 on Windows and cannot encode arrows or middots.
    counters_line = (
        f"{len(turns)} turns, {duration:.0f}s, "
        f"ended on beat {turns[-1].beat_after if turns else 'none'}"
    )
    print(f"\n[playtest:{script.name}] {counters_line}")
    print(f"[playtest:{script.name}] transcript -> {run_dir / 'transcript.md'}")

    failures = runner.failures
    if failures:
        detail = "\n".join(f"  {f}" for f in failures)
        pytest.fail(
            f"{len(failures)} structural check(s) failed in '{script.name}'.\n"
            f"{detail}\n\nFull transcript: {run_dir / 'transcript.md'}",
            pytrace=False,
        )


async def test_smoke_single_turn(runner, ollama_ready, copy_debug_logs):
    """One real turn end to end. Run this first — it proves the ASGI + Ollama
    path works and that artifacts land, without spending ten minutes to find
    out otherwise."""
    script = PlaytestScript(
        name="smoke",
        description="A single real turn against the local LLM.",
        steps=[
            Step(
                label="smoke",
                say="I hold the torch up and look at the archway. What am I walking into?",
                expect=[
                    C.no_error(),
                    C.completed(),
                    C.committed_at_least(1),
                    C.options_between(2, 6),
                ],
            )
        ],
    )
    await _play(runner, script, copy_debug_logs)


async def test_dutiful_playthrough(runner, ollama_ready, copy_debug_logs):
    """Theme 1: follow the pre-scripted adventure to the ending."""
    await _play(runner, DUTIFUL, copy_debug_logs)


async def test_tavern_derail(runner, ollama_ready, copy_debug_logs):
    """Theme 2: abandon the adventure for a tavern that does not exist."""
    await _play(runner, TAVERN_DERAIL, copy_debug_logs)


async def test_trap_and_surrender(runner, ollama_ready, copy_debug_logs):
    """Theme 3: trigger the trap on purpose, then surrender to the boss."""
    await _play(runner, TRAP_AND_SURRENDER, copy_debug_logs)


# ── Option-driven runs ────────────────────────────────────────────────────────
#
# No authored player intent: the model's own options drive the story. Whether
# they get anywhere is the measurement, so "it stalled" is a *result*, not a
# test failure — only turn-level breakage fails these.

async def _play_options(runner, policy, copy_debug_logs, *, max_turns=20, seed=20260806):
    started = datetime.now(timezone.utc)
    script, turns = await runner.run_option_driven(
        policy,
        max_turns=max_turns,
        seed=seed,
        expect=[
            C.no_error(),
            C.completed(),
            C.committed_at_least(1),
            C.options_between(2, 6),
            C.at_most_one_advancing_option(),
            C.stream_matches_save(),
        ],
        probes=baseline_probes(),
    )
    duration = (datetime.now(timezone.utc) - started).total_seconds()

    meta = {
        "model": OLLAMA_MODEL,
        "started": started.isoformat(),
        "save_id": runner.save_id,
        "duration_s": duration,
        "user_name": runner.user_name,
        "policy": policy.name,
        "seed": seed,
        "max_turns": max_turns,
        "stop_reason": runner.stop_reason,
    }

    save = storage.get_save(runner.save_id)
    run_dir = new_run_dir(policy.name)
    report = write_run(
        run_dir, script, turns, meta,
        save_json=save.model_dump_json(indent=2) if save else None,
    )
    copy_debug_logs(run_dir)

    prog = report["counters"].get("progression", {})
    picks = report["counters"].get("option_picks", {})
    print(f"\n[playtest:{policy.name}] {len(turns)} turns, {duration:.0f}s, "
          f"stopped because: {runner.stop_reason}")
    print(f"[playtest:{policy.name}] beat path: {' -> '.join(prog.get('beats_reached_in_order', []))}")
    print(f"[playtest:{policy.name}] took an advancing option on "
          f"{picks.get('picked_an_advancing_option', 0)}/{len(turns)} turns; "
          f"one was offered on {picks.get('turns_offering_an_advancing_option', 0)}")
    print(f"[playtest:{policy.name}] transcript -> {run_dir / 'transcript.md'}")

    failures = runner.failures
    if failures:
        detail = "\n".join(f"  {f}" for f in failures)
        pytest.fail(
            f"{len(failures)} turn-level check(s) failed under '{policy.name}'.\n"
            f"{detail}\n\nFull transcript: {run_dir / 'transcript.md'}",
            pytrace=False,
        )


async def test_always_first_option(runner, ollama_ready, copy_debug_logs):
    """Take the model's first suggestion every turn and see how far it gets."""
    await _play_options(runner, ALWAYS_FIRST, copy_debug_logs)


async def test_random_option(runner, ollama_ready, copy_debug_logs):
    """Take a random suggestion every turn — the meandering baseline."""
    await _play_options(runner, RANDOM_CHOICE, copy_debug_logs)
