"""
Run artifacts.

A playtest produces five files in `tests/playtest/runs/<ts>-<script>/`:

  transcript.md  the human deliverable — every word the LLM produced, in order,
                 with the checks and probe hits interleaved at the turn they fired
  events.jsonl   every SSE event, tagged with its turn
  markers.jsonl  every structured marker, tagged with its turn
  report.json    stable machine summary for diffing runs against each other
  save.json      the final save, i.e. the full persisted message log

The transcript is written incrementally-safe (whole file per call) so a killed
run still leaves whatever completed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.playtest.model import PlaytestScript, TurnResult

RUNS_DIR = Path(__file__).parent / "runs"


def new_run_dir(script_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = RUNS_DIR / f"{stamp}-{script_name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── Transcript ────────────────────────────────────────────────────────────────

def _fmt_option(opt: dict) -> str:
    flags = []
    if opt.get("advances_beat"):
        flags.append("**advances beat**")
    dice = opt.get("dice_roll")
    if dice:
        flags.append(f"`{dice.get('dice')} · {dice.get('difficulty')}`")
    suffix = f" — {', '.join(flags)}" if flags else ""
    return f"- {opt.get('text', '')}{suffix}"


def _fmt_beat(before: str | None, after: str | None) -> str:
    if before == after:
        return f"`{after or 'none'}`"
    return f"`{before or 'none'}` → **`{after or 'none'}`**"


def write_transcript(
    path: Path,
    script: PlaytestScript,
    turns: list[TurnResult],
    meta: dict[str, Any],
    counters: dict[str, Any] | None = None,
) -> None:
    lines: list[str] = [
        f"# Playtest — {script.name}",
        "",
        f"> {script.description}",
        "",
        f"- Model: `{meta.get('model')}`",
        f"- Started: {meta.get('started')}",
        f"- Save: `{meta.get('save_id')}`",
        f"- Turns: {len(turns)} · Wall clock: {meta.get('duration_s', 0):.1f}s",
        "",
    ]

    failures = [c for t in turns for c in t.checks if c.status == "FAIL"]
    lines += [
        "## Verdict",
        "",
        (
            f"**{len(failures)} structural check(s) failed.**"
            if failures
            else "**All structural checks passed.**"
        ),
        "",
    ]

    if counters is None:
        counters = build_report(script, turns, meta)["counters"]
    speaking = counters["speaking_turns"]
    lines += [
        "Speaking turns: "
        + (", ".join(f"{cid} {n}" for cid, n in sorted(speaking.items())) or "nobody"),
        "",
    ]
    if counters["silent_companions"]:
        lines += [
            f"⚠ Never spoke across the whole run: "
            f"**{', '.join(counters['silent_companions'])}** — their persona was "
            f"not exercised. The Director declined to name a speaker on "
            f"{counters['director_declined_speaker']} of {len(turns)} turns.",
            "",
        ]
    for f in failures:
        lines.append(f"- {f}")
    if failures:
        lines.append("")

    probe_hits = [(t.index, h) for t in turns for h in t.probe_hits]
    if probe_hits:
        lines += ["## Probe hits", ""]
        lines += [f"- step {i}: {h}" for i, h in probe_hits]
        lines.append("")

    lines += ["---", ""]

    for turn in turns:
        lines += [
            f"## Step {turn.index} — {turn.label}",
            "",
            f"*beat {_fmt_beat(turn.beat_before, turn.beat_after)}"
            f" · {turn.duration_s:.1f}s"
            + (" · `beat_advance` flag set" if turn.request.get("beat_advance") else "")
            + "*",
            "",
        ]

        if turn.chosen_index is not None:
            chosen = turn.chosen_option or {}
            flags = []
            if chosen.get("advances_beat"):
                flags.append("advances beat")
            if chosen.get("dice_roll"):
                d = chosen["dice_roll"]
                flags.append(f"{d.get('dice')} {d.get('difficulty')}")
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines += [
                f"*Chose option {turn.chosen_index + 1} of "
                f"{len(turn.offered_options)}{suffix}. Also offered:*",
                "",
            ]
            lines += [
                f"- {'**→** ' if i == turn.chosen_index else ''}{o.get('text', '')}"
                for i, o in enumerate(turn.offered_options)
            ]
            lines.append("")

        if turn.user_message:
            lines += ["> **Player:** " + turn.user_message.replace("\n", "\n> "), ""]
        elif turn.request.get("jump_to"):
            lines += [f"*(direct jump to `{turn.request['jump_to']}`)*", ""]

        roll = turn.roll
        if roll:
            lines += [
                f"🎲 **{roll.get('dice')} {roll.get('difficulty')}** — "
                f"rolled {roll.get('value')}/{roll.get('max_value')} "
                f"(need {roll.get('threshold')}) → **{roll.get('outcome')}**",
                "",
            ]

        if turn.error:
            lines += [f"❌ **Turn errored: `{turn.error}`**", ""]

        for msg in turn.messages:
            if msg.character_id is None:
                label = "Beat starter"
            else:
                label = msg.character_name
            body = msg.content.strip() or "*(empty — nothing was generated)*"
            lines += [f"**{label}:**", "", body, ""]

        if not turn.messages and not turn.error and turn.user_message:
            lines += ["*(no messages committed this turn)*", ""]

        if turn.options:
            lines += ["**Options offered:**", ""]
            lines += [_fmt_option(o) for o in turn.options]
            lines.append("")

        noteworthy = [
            c for c in turn.checks if c.status in ("FAIL", "WARN")
        ]
        if noteworthy:
            lines += ["**Checks:**", ""]
            lines += [f"- {c.status}: {c.name} — {c.detail}" for c in noteworthy]
            lines.append("")
        if turn.probe_hits:
            lines += ["**Probes:**", ""]
            lines += [f"- {h}" for h in turn.probe_hits]
            lines.append("")

        lines += ["---", ""]

    path.write_text("\n".join(lines), encoding="utf-8")


# ── Machine-readable ──────────────────────────────────────────────────────────

def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _histogram(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        key = str(v)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _beat_path(turns: list[TurnResult]) -> list[str]:
    """Distinct beats in the order they were first entered."""
    path: list[str] = []
    for t in turns:
        beat = t.beat_after or "none"
        if not path or path[-1] != beat:
            path.append(beat)
    return path


def _turns_per_beat(turns: list[TurnResult]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in turns:
        key = t.beat_after or "none"
        out[key] = out.get(key, 0) + 1
    return out


def build_report(
    script: PlaytestScript,
    turns: list[TurnResult],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Stable schema — this is what you diff between two runs."""
    checks = [c for t in turns for c in t.checks]

    # Who actually got to speak. The Director picks at most one companion per
    # turn, so per-step checks can't tell "not this turn" from "never" — this
    # roll-up can. A companion at 0 across a whole run means their persona was
    # never exercised, which is worth knowing before concluding it works.
    speaking_turns: dict[str, int] = {}
    for t in turns:
        for cid in {m.character_id for m in t.messages if m.character_id}:
            speaking_turns[cid] = speaking_turns.get(cid, 0) + 1

    turns_with_llm_content = sum(
        1 for t in turns if any(m.character_id for m in t.messages)
    )

    counters = {
        "turns": len(turns),
        "speaking_turns": speaking_turns,
        "silent_companions": sorted(
            cid for cid in ("bram", "silvaine") if not speaking_turns.get(cid)
        ),
        "director_declined_speaker": sum(
            1 for t in turns if t.director and t.director.get("speaker_id") is None
        ),
        "llm_calls": sum(
            m.number("llm_calls") for t in turns for m in t.markers_named("turn.end")
        ),
        "beats_visited": len(
            {t.beat_after for t in turns if t.beat_after}
        ),
        "beat_advances": sum(len(t.markers_named("beat.advance")) for t in turns),
        "beat_blocked": sum(len(t.markers_named("beat.blocked")) for t in turns),
        "fallbacks": sum(len(t.markers_named("validation.fallback")) for t in turns),
        "validation_failures": sum(
            len(t.markers_named("validation.failed")) for t in turns
        ),
        "regenerates": sum(len(t.markers_named("speaker.regenerate")) for t in turns),
        "loops_detected": sum(len(t.markers_named("speaker.loop")) for t in turns),
        "empty_speaker_plans": sum(
            len(t.markers_named("speaker.empty_plan")) for t in turns
        ),
        "blank_messages": sum(
            1 for t in turns for m in t.messages if not m.content.strip()
        ),
        "errors": sum(1 for t in turns if t.error),
        "turns_with_generated_content": turns_with_llm_content,
        "ending_reached": any(t.ending_reached for t in turns),
    }

    # Option-driven runs only: did following the model's own suggestions get
    # anywhere, and were the forward-flagged options the ones being taken?
    picks = [t for t in turns if t.chosen_index is not None]
    if picks:
        advancing_offered = sum(
            1 for t in picks if any(o.get("advances_beat") for o in t.offered_options)
        )
        counters["option_picks"] = {
            "turns": len(picks),
            "index_histogram": _histogram(t.chosen_index for t in picks),
            "picked_an_advancing_option": sum(
                1 for t in picks if (t.chosen_option or {}).get("advances_beat")
            ),
            "turns_offering_an_advancing_option": advancing_offered,
            "picked_a_dice_option": sum(
                1 for t in picks if (t.chosen_option or {}).get("dice_roll")
            ),
        }
        counters["progression"] = {
            "beats_reached_in_order": _beat_path(turns),
            "furthest_beat": turns[-1].beat_after if turns else None,
            "turns_per_beat": _turns_per_beat(turns),
            "turns_without_progress": sum(
                1 for t in turns if t.beat_before == t.beat_after
            ),
        }
    return {
        "script": script.name,
        "description": script.description,
        "meta": meta,
        "counters": counters,
        "checks": [
            {
                "step": c.step,
                "label": c.label,
                "name": c.name,
                "status": c.status,
                "detail": c.detail,
            }
            for c in checks
        ],
        "probe_hits": [
            {"step": t.index, "hit": h} for t in turns for h in t.probe_hits
        ],
        "turns": [
            {
                "index": t.index,
                "label": t.label,
                "user_message": t.user_message,
                "request": t.request,
                "duration_s": round(t.duration_s, 2),
                "beat_before": t.beat_before,
                "beat_after": t.beat_after,
                "sandbox_after": t.sandbox_after,
                "roll": t.roll,
                "director": t.director,
                "error": t.error,
                "messages": [
                    {
                        "role": m.role,
                        "character_id": m.character_id,
                        "character_name": m.character_name,
                        "content": m.content,
                    }
                    for m in t.messages
                ],
                "options": t.options,
                "options_context": t.options_context,
                "offered_options": t.offered_options,
                "chosen_index": t.chosen_index,
                "chosen_option": t.chosen_option,
            }
            for t in turns
        ],
    }


def write_run(
    run_dir: Path,
    script: PlaytestScript,
    turns: list[TurnResult],
    meta: dict[str, Any],
    save_json: str | None = None,
) -> dict[str, Any]:
    """Write every artifact and return the report dict, so callers can print a
    summary without recomputing it."""
    report = build_report(script, turns, meta)
    write_transcript(
        run_dir / "transcript.md", script, turns, meta, counters=report["counters"]
    )
    write_jsonl(
        run_dir / "events.jsonl",
        [
            {"turn": t.index, "event": name, "data": payload}
            for t in turns
            for name, payload in t.events
        ],
    )
    write_jsonl(
        run_dir / "markers.jsonl",
        [{"step": t.index, **m.as_dict()} for t in turns for m in t.markers],
    )
    (run_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if save_json:
        (run_dir / "save.json").write_text(save_json, encoding="utf-8")
    return report
