"""
Scenario CRUD with nested beat handling.

A scenario document carries its beats inline. A single PUT replaces the whole
beats list. After save we walk the beats and schedule a background summary
regen for any whose `summary_hash` no longer matches their content.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.background import BackgroundTasks as _BG  # noqa: F401  (silence linter)
from pydantic import BaseModel, ConfigDict

from app import storage, summarizer
from app.defaults import DEFAULT_SCENARIO_SYSTEM_PROMPT
from app.models import Scenario

logger = logging.getLogger(__name__)
router = APIRouter()


class ScenarioSummaryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    summary: str
    beat_count: int
    has_beats: bool


class DeleteResult(BaseModel):
    deleted: bool
    in_use_by_saves: bool


class RegenResult(BaseModel):
    summary: str


# ── List / get ────────────────────────────────────────────────────────────────

@router.get("/scenarios", response_model=list[ScenarioSummaryRow])
def list_scenarios_route() -> list[ScenarioSummaryRow]:
    return [
        ScenarioSummaryRow(
            id=s.id,
            name=s.name,
            summary=s.summary,
            beat_count=len(s.beats),
            has_beats=bool(s.beats),
        )
        for s in storage.list_scenarios()
    ]


@router.get("/scenarios/{scenario_id}", response_model=Scenario)
def get_scenario_route(scenario_id: str) -> Scenario:
    scenario = storage.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    return scenario


# ── Create / update / delete ──────────────────────────────────────────────────

def _normalize_beats(scenario: Scenario) -> Scenario:
    """Assign UUIDs to any beat without an id, renumber `order`, stamp hashes."""
    for idx, beat in enumerate(scenario.beats):
        if not beat.id:
            beat.id = str(uuid.uuid4())
        beat.order = idx
    summarizer.stamp_scenario_hash(scenario)
    return scenario


def _schedule_stale_beat_regens(
    scenario_id: str,
    new_scenario: Scenario,
    old_scenario: Optional[Scenario],
    background_tasks: BackgroundTasks,
) -> None:
    """Schedule a beat summary regen for any beat whose source content has changed."""
    old_by_id = {b.id: b for b in old_scenario.beats} if old_scenario else {}
    for beat in new_scenario.beats:
        old = old_by_id.get(beat.id)
        if old is None:
            background_tasks.add_task(
                summarizer.regen_beat_if_stale, scenario_id, beat.id
            )
            continue
        old_hash = summarizer.beat_summary_hash(
            old.name, old.description, old.transition_condition
        )
        new_hash = summarizer.beat_summary_hash(
            beat.name, beat.description, beat.transition_condition
        )
        if old_hash != new_hash or not beat.summary:
            background_tasks.add_task(
                summarizer.regen_beat_if_stale, scenario_id, beat.id
            )


@router.post("/scenarios", response_model=Scenario)
def create_scenario_route(
    payload: Scenario, background_tasks: BackgroundTasks
) -> Scenario:
    if not payload.id:
        payload.id = str(uuid.uuid4())
    if storage.get_scenario(payload.id) is not None:
        raise HTTPException(409, "Scenario with this id already exists")

    if not payload.system_prompt:
        payload.system_prompt = DEFAULT_SCENARIO_SYSTEM_PROMPT

    _normalize_beats(payload)
    storage.save_scenario(payload)

    background_tasks.add_task(summarizer.regen_scenario_if_stale, payload.id)
    for beat in payload.beats:
        background_tasks.add_task(
            summarizer.regen_beat_if_stale, payload.id, beat.id
        )
    return payload


@router.put("/scenarios/{scenario_id}", response_model=Scenario)
def update_scenario_route(
    scenario_id: str, payload: Scenario, background_tasks: BackgroundTasks
) -> Scenario:
    if payload.id != scenario_id:
        raise HTTPException(400, "Path id and body id must match")
    existing = storage.get_scenario(scenario_id)
    if existing is None:
        raise HTTPException(404, "Scenario not found")

    new_summary_hash = summarizer.scenario_summary_hash(
        payload.initial_message, payload.system_prompt
    )
    scenario_summary_changed = new_summary_hash != existing.summary_hash

    # Preserve existing summary if the user hasn't manually changed it.
    if not scenario_summary_changed and not payload.summary:
        payload.summary = existing.summary

    _normalize_beats(payload)
    payload.summary_hash = new_summary_hash

    storage.save_scenario(payload)
    if scenario_summary_changed:
        background_tasks.add_task(summarizer.regen_scenario_if_stale, scenario_id)
    _schedule_stale_beat_regens(scenario_id, payload, existing, background_tasks)
    return payload


@router.delete("/scenarios/{scenario_id}", response_model=DeleteResult)
def delete_scenario_route(scenario_id: str) -> DeleteResult:
    in_use = storage.is_scenario_in_use(scenario_id)
    deleted = storage.delete_scenario(scenario_id)
    if not deleted:
        raise HTTPException(404, "Scenario not found")
    if in_use:
        logger.warning(
            "Deleted scenario %s but it is still referenced by at least one save",
            scenario_id,
        )
    return DeleteResult(deleted=True, in_use_by_saves=in_use)


# ── Manual regeneration ───────────────────────────────────────────────────────

@router.post("/scenarios/{scenario_id}/regenerate-summary", response_model=RegenResult)
async def regenerate_scenario_summary_route(scenario_id: str) -> RegenResult:
    summary = await summarizer.regenerate_scenario_summary_sync(scenario_id)
    if summary is None:
        raise HTTPException(404, "Scenario not found")
    return RegenResult(summary=summary)


@router.post(
    "/scenarios/{scenario_id}/beats/{beat_id}/regenerate-summary",
    response_model=RegenResult,
)
async def regenerate_beat_summary_route(scenario_id: str, beat_id: str) -> RegenResult:
    summary = await summarizer.regenerate_beat_summary_sync(scenario_id, beat_id)
    if summary is None:
        raise HTTPException(404, "Scenario or beat not found")
    return RegenResult(summary=summary)
