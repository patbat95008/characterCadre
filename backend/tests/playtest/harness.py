"""
The playtest runner.

Drives real `/api/chat/turn` calls against the real local LLM (no mocks) and
records everything observable about each turn: the SSE stream, the marker
stream, the committed prose, the options offered, and how long it took.

Two independent views of what the LLM said are captured on purpose:

  * `TurnResult.streamed` — reassembled from `token` events, i.e. exactly what a
    player watching the screen would have seen, with regenerated drafts
    discarded the way the frontend discards them.
  * `TurnResult.messages` — diffed out of the save file after the turn, i.e.
    what was actually persisted.

They should agree. When they don't, something is wrong with the streaming path,
so the runner records the discrepancy rather than trusting either one.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Optional

from httpx import AsyncClient

from app import storage
from app.markers import Marker, parse as parse_marker
from app.fixtures import BRAM, NARRATOR, SCENARIO, SILVAINE

from tests.playtest.model import (
    CheckResult,
    OptionPolicy,
    PlaytestScript,
    SpokenMessage,
    Step,
    TurnResult,
)

logger = logging.getLogger(__name__)


# ── SSE ───────────────────────────────────────────────────────────────────────

def parse_sse(text: str) -> list[tuple[str, Any]]:
    """Parse an SSE body into [(event_name, data_dict_or_str), ...].

    `done` carries an empty data line, which is not valid JSON, so parsing is
    deliberately forgiving and falls back to the raw string.
    """
    events: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:]
        try:
            parsed: Any = json.loads(data)
        except (ValueError, json.JSONDecodeError):
            parsed = data
        events.append((name, parsed))
    return events


def reassemble_streamed(events: list[tuple[str, Any]]) -> dict[str, str]:
    """Rebuild each speaker's visible text from token events.

    Mirrors the frontend: tokens are provisional until `message_complete`, and a
    `regenerate` throws away everything buffered for that character so far.
    """
    buffers: dict[str, list[str]] = {}
    committed: dict[str, list[str]] = {}
    for name, payload in events:
        if not isinstance(payload, dict):
            continue
        cid = payload.get("character_id") or "?"
        if name == "token":
            buffers.setdefault(cid, []).append(payload.get("text", ""))
        elif name == "regenerate":
            buffers[cid] = []
        elif name == "message_complete":
            committed.setdefault(cid, []).append("".join(buffers.get(cid, [])))
            buffers[cid] = []
    return {cid: "\n\n".join(parts) for cid, parts in committed.items()}


# ── Marker capture ────────────────────────────────────────────────────────────

class MarkerSink(logging.Handler):
    """Collects marker records emitted in-process during a turn."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[Marker] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        marker = parse_marker(record.getMessage())
        if marker is not None:
            self.records.append(marker)

    def drain(self) -> list[Marker]:
        out = self.records
        self.records = []
        return out


# ── Runner ────────────────────────────────────────────────────────────────────

CHARACTER_NAMES = {
    NARRATOR.id: NARRATOR.name,
    BRAM.id: BRAM.name,
    SILVAINE.id: SILVAINE.name,
}


class PlaytestRunner:
    def __init__(self, client: AsyncClient, sink: MarkerSink, user_name: str = "Kestrel"):
        self.client = client
        self.sink = sink
        self.user_name = user_name
        self.save_id: str = ""
        self.turns: list[TurnResult] = []
        self.opening_options: list[dict] = []
        self.stop_reason: str = ""

    # ── Setup ────────────────────────────────────────────────────────────────

    async def create_save(self) -> str:
        r = await self.client.post("/api/saves", json={
            "scenario_id": SCENARIO.id,
            "active_character_ids": [NARRATOR.id, BRAM.id, SILVAINE.id],
            "user_name": self.user_name,
            "name": "Playtest",
        })
        r.raise_for_status()
        self.save_id = r.json()["id"]
        self.sink.drain()
        return self.save_id

    async def seed_options(self) -> list[dict]:
        """Fetch the opening options the UI would show before the first turn."""
        r = await self.client.get(f"/api/saves/{self.save_id}/seed-options")
        r.raise_for_status()
        self.opening_options = r.json().get("options", [])
        self.sink.drain()
        return self.opening_options

    def get_save(self):
        save = storage.get_save(self.save_id)
        assert save is not None, f"save {self.save_id} vanished mid-run"
        return save

    # ── Step execution ───────────────────────────────────────────────────────

    async def _ensure_save(self) -> None:
        """Create and seed a save unless the caller already set one up.

        Lets a run start from a prepared state — mid-adventure, on the final
        beat — instead of always from the scenario's opening message.
        """
        if not self.save_id:
            await self.create_save()
            await self.seed_options()

    async def run_script(self, script: PlaytestScript) -> list[TurnResult]:
        await self._ensure_save()
        for index, step in enumerate(script.steps, start=1):
            result = await self.run_step(index, step)
            self.turns.append(result)
            if result.error:
                # A turn that errored leaves the save un-persisted; continuing
                # would test a state the app never actually reached.
                logger.error(
                    "playtest step %d errored (%s) — stopping script %s",
                    index, result.error, script.name,
                )
                break
        return self.turns

    async def run_option_driven(
        self,
        policy: OptionPolicy,
        *,
        max_turns: int = 20,
        seed: int = 20260806,
        expect: list | None = None,
        probes: list | None = None,
    ) -> tuple[PlaytestScript, list[TurnResult]]:
        """Let the model's own options drive the story, with no authored intent.

        Clicking an option in the UI sends its text, its `advances_beat` as the
        beat-advance flag, and its `dice_roll` (frontend `Game.tsx`:
        `send(opt.text, opt.advances_beat, opt.dice_roll)`) — this mirrors that
        exactly, so the run reflects what a real player clicking through would
        get.

        Stops on `ending_reached`, on a turn error, when no options come back,
        or after `max_turns`. The RNG is seeded and recorded so a random walk
        can be replayed.
        """
        rng = random.Random(seed)
        script = PlaytestScript(
            name=policy.name,
            description=policy.description,
        )

        await self._ensure_save()
        options = self.turns[-1].options if self.turns else self.opening_options

        stop_reason = f"hit max_turns ({max_turns})"
        for index in range(1, max_turns + 1):
            if not options:
                stop_reason = "no options were offered"
                break

            choice = policy.pick(options, rng)
            choice = max(0, min(choice, len(options) - 1))
            option = options[choice]

            step = Step(
                say=option.get("text", ""),
                label=f"[{choice + 1}/{len(options)}] {option.get('text', '')}",
                beat_advance=bool(option.get("advances_beat")),
                dice_roll=option.get("dice_roll"),
                expect=list(expect or []),
                probes=list(probes or []),
            )
            script.steps.append(step)

            result = await self.run_step(index, step)
            result.offered_options = list(options)
            result.chosen_index = choice
            result.chosen_option = option
            self.turns.append(result)

            if result.error:
                stop_reason = f"turn errored ({result.error})"
                break
            if result.ending_reached:
                stop_reason = "reached the ending"
                break
            options = result.options

        logger.info(
            "option-driven run '%s' stopped after %d turns: %s",
            policy.name, len(self.turns), stop_reason,
        )
        self.stop_reason = stop_reason
        return script, self.turns

    async def run_step(self, index: int, step: Step) -> TurnResult:
        if step.jump_to:
            return await self._run_jump(index, step)

        attempts = step.retry_on_dice_failure + 1
        result: Optional[TurnResult] = None
        for attempt in range(attempts):
            result = await self._run_turn(index, step)
            roll = result.roll
            failed_roll = roll is not None and roll.get("outcome") in (
                "failure", "critical_failure"
            )
            if not failed_roll or attempt == attempts - 1:
                break
            logger.info(
                "playtest step %d: roll failed (%s), retrying (%d/%d)",
                index, roll.get("outcome") if roll else "?", attempt + 1, attempts,
            )
        assert result is not None
        self._evaluate(result, step)
        return result

    async def _run_jump(self, index: int, step: Step) -> TurnResult:
        """Advance the beat directly, no LLM involved."""
        save = self.get_save()
        result = TurnResult(
            index=index,
            label=step.label or f"jump → {step.jump_to}",
            user_message="",
            request={"jump_to": step.jump_to},
            beat_before=save.current_beat_id,
        )
        started = time.monotonic()
        r = await self.client.post(
            f"/api/saves/{self.save_id}/advance-beat",
            json={"next_beat_id": step.jump_to, "wipe_context": False},
        )
        result.duration_s = time.monotonic() - started
        result.markers = self.sink.drain()
        if r.status_code != 200:
            result.checks.append(CheckResult(
                name="jump_succeeded", status="FAIL", step=index, label=result.label,
                detail=f"advance-beat returned {r.status_code}: {r.text[:200]}",
            ))
            return result
        after = self.get_save()
        result.beat_after = after.current_beat_id
        result.sandbox_after = after.sandbox_mode
        result.messages = self._diff_messages(save, after)
        self._evaluate(result, step)
        return result

    async def _run_turn(self, index: int, step: Step) -> TurnResult:
        before = self.get_save()
        body: dict[str, Any] = {
            "user_message": step.say,
            "save_id": self.save_id,
            "beat_advance": step.beat_advance,
        }
        dice = step.dice_roll or (self._offered_dice() if step.use_offered_dice else None)
        if dice:
            body["dice_roll"] = dice

        result = TurnResult(
            index=index,
            label=step.label or step.say[:48],
            user_message=step.say,
            request=body,
            beat_before=before.current_beat_id,
        )

        started = time.monotonic()
        chunks: list[str] = []
        async with self.client.stream("POST", "/api/chat/turn", json=body) as r:
            if r.status_code != 200:
                raw = await r.aread()
                result.duration_s = time.monotonic() - started
                result.checks.append(CheckResult(
                    name="turn_http_ok", status="FAIL", step=index, label=result.label,
                    detail=f"HTTP {r.status_code}: {raw[:200]!r}",
                ))
                return result
            async for chunk in r.aiter_text():
                chunks.append(chunk)
        result.duration_s = time.monotonic() - started

        result.events = parse_sse("".join(chunks))
        result.markers = self.sink.drain()
        result.streamed = reassemble_streamed(result.events)

        options_payload = result.first("options")
        if isinstance(options_payload, dict):
            result.options = options_payload.get("options", [])
        ctx_payload = result.first("options_context")
        if isinstance(ctx_payload, dict):
            result.options_context = ctx_payload.get("context", "")

        after = self.get_save()
        result.beat_after = after.current_beat_id
        result.sandbox_after = after.sandbox_mode
        result.messages = self._diff_messages(before, after)
        return result

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _offered_dice(self) -> Optional[dict]:
        """The dice spec attached to an option the player was last shown.

        Walks back rather than looking only at `turns[-1]`: a `jump_to` step
        produces no options, so the immediately-preceding entry is often empty
        and a naive lookup silently drops the roll.
        """
        for turn in reversed(self.turns):
            if turn.options:
                for opt in turn.options:
                    if opt.get("dice_roll"):
                        return opt["dice_roll"]
                return None  # they were offered options, just none with a roll
        for opt in self.opening_options:
            if opt.get("dice_roll"):
                return opt["dice_roll"]
        return None

    @staticmethod
    def _diff_messages(before, after) -> list[SpokenMessage]:
        """Messages the turn added, excluding the player's own and dm-only context."""
        known = {m.id for m in before.messages}
        out: list[SpokenMessage] = []
        for msg in after.messages:
            if msg.id in known or msg.role == "user" or msg.is_dm_only:
                continue
            out.append(SpokenMessage(
                message_id=msg.id,
                role=msg.role,
                character_id=msg.character_id,
                character_name=CHARACTER_NAMES.get(
                    msg.character_id or "", "(beat starter)" if msg.character_id is None else msg.character_id or "?"
                ),
                content=msg.content,
            ))
        return out

    def _evaluate(self, result: TurnResult, step: Step) -> None:
        for check in step.expect:
            outcome = check(result)
            outcome.step = result.index
            outcome.label = result.label
            result.checks.append(outcome)
        for probe in step.probes:
            hit = probe(result)
            if hit:
                result.probe_hits.append(hit)

    # ── Aggregates ───────────────────────────────────────────────────────────

    @property
    def all_checks(self) -> list[CheckResult]:
        return [c for turn in self.turns for c in turn.checks]

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.all_checks if c.failed]
