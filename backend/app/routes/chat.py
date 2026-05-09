"""
Multi-phase chat turn endpoint, generalised across saves.

Loads the target save by id, resolves its scenario and active characters from
storage, then runs Phase 1 (Director) → optional beat transition → Phase 2 (DM
+ character streaming) → Phase 3 (player option drafting), emitting SSE events
throughout.

The Stage 2 turn endpoint (turn.py) was hard-coded to the stage1 fixture; this
replacement reads everything from the per-entity storage layer instead.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app import storage
from app.models import Character, Message, TurnRequest
from app.ollama_client import OllamaTimeoutError, OllamaUnreachableError
from app.phases import (
    apply_beat_transition,
    is_final_beat_completion,
    run_director,
    run_phase2,
    run_phase3,
)
from app.validation import OPTIONS_FALLBACK

logger = logging.getLogger(__name__)
router = APIRouter()


def _sse(event: str, data: dict | str) -> str:
    payload = json.dumps(data) if isinstance(data, dict) else data
    return f"event: {event}\ndata: {payload}\n\n"


def _load_active_characters(save) -> dict[str, Character]:
    """Return a {id: Character} map for every active character in the save."""
    out: dict[str, Character] = {}
    for cid in save.active_character_ids:
        c = storage.get_character(cid)
        if c is not None:
            out[cid] = c
        else:
            logger.warning("save references missing character %s (save=%s)", cid, save.id)
    return out


@router.post("/chat/turn")
async def chat_turn(request: TurnRequest) -> StreamingResponse:
    """
    Multi-phase turn endpoint. SSE events emitted (in order):
      event: director           data: {speaker_id, dm_narrating, direction_note}
      event: beat_transition    data: {new_beat_id, new_beat_name}      (if transition)
      event: ending_reached     data: {}                                 (if final beat done)
      event: token              data: {character_id, text}              (repeated)
      event: message_complete   data: {message_id, character_id}
      event: regenerate         data: {reason}                          (mid-stream retry)
      event: validation_warning data: {reason}
      event: validation_failed  data: {call, reason}
      event: options            data: {options: [...]}
      event: error              data: {reason}
      event: done               data: ""
    """
    save = storage.get_save(request.save_id)
    if save is None:
        raise HTTPException(404, f"Save {request.save_id} not found")
    scenario = storage.get_scenario(save.scenario_id)
    if scenario is None:
        raise HTTPException(404, f"Scenario {save.scenario_id} not found for save")
    characters = _load_active_characters(save)

    async def generate():
        start_time = datetime.now(timezone.utc)

        user_msg = Message(
            id=str(uuid.uuid4()),
            role="user",
            character_id=None,
            content=request.user_message,
            timestamp=start_time.isoformat(),
            beat_id_at_time=save.current_beat_id,
        )
        save.messages.append(user_msg)

        try:
            # ── Phase 1: Director ─────────────────────────────────────────────
            director_failures: list[dict] = []

            async def on_director_retry(reason: str) -> None:
                director_failures.append({"call": "director", "reason": reason})

            director_response = await run_director(
                save, scenario, characters, on_retry=on_director_retry
            )

            for evt in director_failures:
                yield _sse("validation_failed", evt)

            logger.info(
                "director decision (save=%s): speaker=%s dm_narrate=%s beat_transition=%s next_beat=%s | %s",
                save.id,
                director_response.speaker_character_id or "none",
                director_response.dm_should_narrate,
                director_response.beat_transition,
                director_response.next_beat_id or "none",
                director_response.direction_note or "(no note)",
            )

            yield _sse("director", {
                "speaker_id": director_response.speaker_character_id,
                "dm_narrating": director_response.dm_should_narrate,
                "direction_note": director_response.direction_note,
            })

            # ── Beat transition + ending detection ────────────────────────────
            ending = is_final_beat_completion(save, scenario, director_response)
            beat_data = apply_beat_transition(save, scenario, director_response)
            if beat_data:
                yield _sse("beat_transition", beat_data)
            if ending:
                save.sandbox_mode = True
                yield _sse("ending_reached", {})
                logger.info("ending reached (save=%s) — sandbox mode enabled", save.id)

            # ── Phase 2: stream DM + character ────────────────────────────────
            phase2_gen = await run_phase2(save, scenario, characters, director_response)
            async for event_dict in phase2_gen:
                event_name = event_dict["event"]
                payload = {k: v for k, v in event_dict.items() if k != "event"}
                yield _sse(event_name, payload)

        except OllamaUnreachableError as exc:
            logger.warning("Ollama unreachable during turn (save=%s): %s", save.id, exc)
            yield _sse("error", {"reason": "ollama_unreachable"})
            save.messages.pop()
            return
        except OllamaTimeoutError as exc:
            logger.warning("Ollama timeout during turn (save=%s): %s", save.id, exc)
            yield _sse("error", {"reason": "ollama_timeout"})
            save.messages.pop()
            return
        except Exception as exc:
            logger.error(
                "Unhandled error during turn (save=%s): %s", save.id, exc, exc_info=True
            )
            yield _sse("error", {"reason": "internal_error"})
            save.messages.pop()
            return

        storage.save_save(save)

        duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        logger.info("turn completed (save=%s, duration=%dms)", save.id, duration_ms)

        # ── Phase 3 ───────────────────────────────────────────────────────────
        options_failures: list[dict] = []

        async def on_options_retry(reason: str) -> None:
            options_failures.append({"call": "options", "reason": reason})

        try:
            options, options_context = await run_phase3(
                save, scenario, characters,
                direction_note=director_response.direction_note or None,
                on_retry=on_options_retry,
            )
        except Exception as exc:
            logger.warning("Phase 3 failed (save=%s): %s", save.id, exc)
            options = list(OPTIONS_FALLBACK)
            options_context = ""

        for evt in options_failures:
            yield _sse("validation_failed", evt)

        if options_context:
            yield _sse("options_context", {"context": options_context})
        yield _sse("options", {"options": options})
        yield _sse("done", "")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/saves/{save_id}/seed-options")
async def seed_options_route(save_id: str) -> dict:
    """Generate opening player options without consuming a user turn.

    Used right after a save is created so the UI can show 4 starting options.
    """
    save = storage.get_save(save_id)
    if save is None:
        raise HTTPException(404, "Save not found")
    scenario = storage.get_scenario(save.scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found for save")
    characters = _load_active_characters(save)
    options, context = await run_phase3(save, scenario, characters)
    return {"options": options, "context": context}
