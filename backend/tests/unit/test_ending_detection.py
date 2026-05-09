"""Unit tests for is_final_beat_completion in phases.py."""
from __future__ import annotations

from app.fixtures import make_stage1_save
from app.models import Beat, DirectorResponse, Scenario
from app.phases import is_final_beat_completion


def _scenario_with_beats(*beats: Beat) -> Scenario:
    s = make_stage1_save()
    base = Scenario(
        id="x",
        name="x",
        initial_message="hello",
        system_prompt="prompt",
        beats=list(beats),
    )
    return base


def _save_in_beat(beat_id: str | None):
    save = make_stage1_save()
    save.current_beat_id = beat_id
    return save


def _director(**kwargs) -> DirectorResponse:
    return DirectorResponse(
        speaker_character_id="bram",
        dm_should_narrate=False,
        beat_transition=kwargs.get("beat_transition", False),
        next_beat_id=kwargs.get("next_beat_id"),
        direction_note="",
    )


def _b(order: int, bid: str | None = None) -> Beat:
    return Beat(
        id=bid or f"b{order}",
        order=order,
        name=f"Beat {order}",
        description="d",
        transition_condition="t",
        starter_prompt="s",
    )


def test_returns_false_when_scenario_has_no_beats():
    save = _save_in_beat(None)
    sc = _scenario_with_beats()
    assert is_final_beat_completion(save, sc, _director(beat_transition=True)) is False


def test_returns_false_when_save_not_in_last_beat():
    save = _save_in_beat("b0")
    sc = _scenario_with_beats(_b(0), _b(1))
    assert is_final_beat_completion(save, sc, _director(beat_transition=True)) is False


def test_returns_false_when_no_transition_signaled():
    save = _save_in_beat("b1")
    sc = _scenario_with_beats(_b(0), _b(1))
    assert is_final_beat_completion(save, sc, _director(beat_transition=False)) is False


def test_returns_true_when_last_beat_with_transition_signaled():
    save = _save_in_beat("b1")
    sc = _scenario_with_beats(_b(0), _b(1))
    assert is_final_beat_completion(save, sc, _director(beat_transition=True)) is True


def test_returns_true_when_director_points_at_same_beat():
    save = _save_in_beat("b1")
    sc = _scenario_with_beats(_b(0), _b(1))
    assert is_final_beat_completion(
        save, sc, _director(beat_transition=True, next_beat_id="b1")
    ) is True


def test_returns_false_when_director_picks_other_beat_while_in_last():
    """Pointing at a different beat while in the last is invalid (would be backward) — not an ending."""
    save = _save_in_beat("b1")
    sc = _scenario_with_beats(_b(0), _b(1))
    assert is_final_beat_completion(
        save, sc, _director(beat_transition=True, next_beat_id="b0")
    ) is False
