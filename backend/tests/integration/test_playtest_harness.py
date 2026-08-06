"""
Offline tests for the playtest harness itself.

The live playthroughs in tests/playtest/ can only run with Ollama up, so the
harness would otherwise be untested in the normal suite — and a broken harness
looks exactly like a misbehaving model. These drive PlaytestRunner end to end
with Ollama mocked, so the plumbing (SSE reassembly, save diffing, checks,
report writing) is covered without a model.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import storage
from app.markers import MARKER_LOGGER_NAME
from tests.playtest import checks as C
from tests.playtest.harness import (
    MarkerSink,
    PlaytestRunner,
    parse_sse,
    reassemble_streamed,
)
from tests.playtest.model import OptionPolicy, PlaytestScript, Step
from tests.playtest.report import build_report, write_run
from tests.playtest.scripts import ALWAYS_FIRST, RANDOM_CHOICE


# ── SSE plumbing ──────────────────────────────────────────────────────────────

class TestParseSse:

    def test_parses_named_events_and_json_payloads(self):
        body = (
            "event: director\ndata: {\"speaker_id\": \"bram\"}\n\n"
            "event: token\ndata: {\"character_id\": \"bram\", \"text\": \"Aye.\"}\n\n"
        )
        events = parse_sse(body)
        assert events[0] == ("director", {"speaker_id": "bram"})
        assert events[1][1]["text"] == "Aye."

    def test_done_carries_a_non_json_payload_without_blowing_up(self):
        events = parse_sse("event: done\ndata: \n\n")
        assert events == [("done", "")]


class TestReassembleStreamed:

    def test_tokens_are_joined_per_character(self):
        events = [
            ("token", {"character_id": "bram", "text": "Aye. "}),
            ("token", {"character_id": "bram", "text": "Sulfur."}),
            ("message_complete", {"character_id": "bram", "message_id": "m1"}),
        ]
        assert reassemble_streamed(events) == {"bram": "Aye. Sulfur."}

    def test_regenerate_discards_the_abandoned_draft(self):
        # Phase 2 emits tokens optimistically and can re-stream up to three
        # times; only the run that reaches message_complete is real.
        events = [
            ("token", {"character_id": "bram", "text": "junk draft"}),
            ("regenerate", {"character_id": "bram", "reason": "empty"}),
            ("token", {"character_id": "bram", "text": "the real line"}),
            ("message_complete", {"character_id": "bram", "message_id": "m1"}),
        ]
        assert reassemble_streamed(events) == {"bram": "the real line"}

    def test_two_speakers_are_kept_apart(self):
        events = [
            ("token", {"character_id": "narrator", "text": "The air is stale."}),
            ("message_complete", {"character_id": "narrator", "message_id": "m1"}),
            ("token", {"character_id": "bram", "text": "Watch your step, lad."}),
            ("message_complete", {"character_id": "bram", "message_id": "m2"}),
        ]
        assert reassemble_streamed(events) == {
            "narrator": "The air is stale.",
            "bram": "Watch your step, lad.",
        }


# ── End-to-end with a mocked model ────────────────────────────────────────────

def _director_raw(speaker_id, *, beat_transition=False, next_beat_id=None) -> dict:
    return {
        "speaker_character_id": speaker_id,
        "dm_should_narrate": True,
        "beat_transition": beat_transition,
        "next_beat_id": next_beat_id,
        "direction_note": "Keep it moving.",
        "reasoning": "Test.",
    }


def _options_raw() -> dict:
    return {"options": [
        {"text": "Wait.", "advances_beat": False, "dice_roll": None},
        {"text": "Press on.", "advances_beat": True, "dice_roll": None},
        {"text": "Lift the flagstone.", "advances_beat": False,
         "dice_roll": {"dice": "D20", "difficulty": "Medium"}},
        {"text": "Ask Bram.", "advances_beat": False, "dice_roll": None},
    ]}


@pytest.fixture
def sink():
    handler = MarkerSink()
    logger = logging.getLogger(MARKER_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


@pytest.fixture
async def harness_client():
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _mocked_llm(director_raw: dict, narration: str = "The corridor narrows ahead."):
    async def mock_stream(model, messages, **kwargs):
        yield narration

    return (
        patch("app.phases.structured_chat", new=AsyncMock(
            side_effect=lambda model, msgs, schema, **kw: (
                director_raw
                if "dm_should_narrate" in json.dumps(schema)
                else _options_raw()
            ),
        )),
        patch("app.phases.stream_chat", side_effect=mock_stream),
    )


class TestRunnerEndToEnd:

    async def test_runs_a_script_and_records_everything(self, harness_client, sink, tmp_path):
        runner = PlaytestRunner(harness_client, sink)
        script = PlaytestScript(
            name="harness-selftest",
            description="Two turns against a mocked model.",
            steps=[
                Step(
                    label="look around",
                    say="I hold the torch up and look at the archway.",
                    expect=[
                        C.no_error(),
                        C.completed(),
                        C.committed_at_least(1),
                        C.options_between(2, 6),
                        C.at_most_one_advancing_option(),
                        C.stream_matches_save(),
                        C.no_blank_messages(),
                    ],
                ),
                Step(
                    label="press on",
                    say="We press on, deeper in.",
                    expect=[C.no_error(), C.no_fallback()],
                ),
            ],
        )

        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            turns = await runner.run_script(script)

        assert len(turns) == 2
        assert runner.failures == [], [str(f) for f in runner.failures]

        first = turns[0]
        # The DM narrates and Bram answers, so two messages land.
        assert len(first.messages) == 2
        assert {m.character_id for m in first.messages} == {"narrator", "bram"}
        assert first.messages[0].character_name == "The Narrator"
        assert first.options and len(first.options) == 4
        # Markers were captured in-process and are attributed to this turn.
        assert first.markers_named("turn.start")
        assert first.markers_named("turn.end")

    async def test_offered_dice_is_replayed_on_the_next_turn(self, harness_client, sink):
        runner = PlaytestRunner(harness_client, sink)
        script = PlaytestScript(
            name="dice-replay",
            description="Take the option the model attached a roll to.",
            steps=[
                Step(say="I look at the flagstones.", label="look"),
                Step(
                    say="I lift the flagstone clear.",
                    label="lift",
                    use_offered_dice=True,
                    expect=[C.advance_matches_dice_outcome()],
                ),
            ],
        )

        structured, stream = _mocked_llm(_director_raw("silvaine"))
        with structured, stream:
            turns = await runner.run_script(script)

        # The mocked options attach D20/Medium to option 2; the runner should
        # have picked it up and sent it with the second turn.
        assert turns[1].request["dice_roll"] == {"dice": "D20", "difficulty": "Medium"}
        assert turns[1].roll is not None
        assert turns[1].roll["dice"] == "D20"

    async def test_offered_dice_survives_an_intervening_jump_step(self, harness_client, sink):
        # A jump produces no options. Looking only at the previous entry meant
        # a `use_offered_dice` step right after a jump silently rolled nothing —
        # which is how the trap script ran without ever rolling for the trap.
        runner = PlaytestRunner(harness_client, sink)
        script = PlaytestScript(
            name="dice-after-jump",
            description="Jump between being offered a roll and taking it.",
            steps=[
                Step(say="I look at the flagstones.", label="look"),
                Step(jump_to="beat-trapped-corridor", label="jump"),
                Step(say="I stamp on the plate.", label="stamp", use_offered_dice=True),
            ],
        )

        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            turns = await runner.run_script(script)

        assert turns[1].options == [], "a jump should not produce options"
        assert turns[2].request["dice_roll"] == {"dice": "D20", "difficulty": "Medium"}
        assert turns[2].roll is not None

    async def test_jump_step_advances_without_calling_the_model(self, harness_client, sink):
        runner = PlaytestRunner(harness_client, sink)
        script = PlaytestScript(
            name="jump",
            description="Skip ahead to a set piece.",
            steps=[Step(jump_to="beat-trapped-corridor", label="jump to the trap")],
        )

        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            turns = await runner.run_script(script)

        assert turns[0].beat_before is None
        assert turns[0].beat_after == "beat-trapped-corridor"
        # The beat's starter prompt is committed as a DM message.
        assert any("flagstones" in m.content for m in turns[0].messages)
        assert turns[0].markers_named("beat.advance")[0].get("trigger") == "manual_soft"

    async def test_the_app_now_refuses_a_first_transition_that_skips_beats(
        self, harness_client, sink
    ):
        # validate_director_response rejects "open on a later beat" outright, so
        # the harness check should have nothing to catch. Kept as a regression
        # guard: if the validator ever loosens, this turns red.
        runner = PlaytestRunner(harness_client, sink)
        script = PlaytestScript(
            name="skip",
            description="Director tries to jump straight to the final beat.",
            steps=[
                Step(
                    say="I walk in.",
                    label="walk in",
                    expect=[C.first_transition_is_lowest_order("beat-entry-hall")],
                )
            ],
        )

        structured, stream = _mocked_llm(
            _director_raw("bram", beat_transition=True, next_beat_id="beat-adventure-complete")
        )
        with structured, stream:
            turns = await runner.run_script(script)

        assert turns[0].beat_after is None, "the skip should have been rejected"
        assert runner.failures == []

    def test_the_check_itself_still_fails_a_skipping_transition(self):
        # The app-level fix means no live run can produce this any more, so the
        # check is exercised directly rather than through a turn.
        from app.markers import Marker
        from tests.playtest.model import TurnResult

        turn = TurnResult(
            index=1, label="walk in", user_message="I walk in.", request={},
            beat_before=None, beat_after="beat-adventure-complete",
            markers=[Marker(event="beat.advance", turn="t1",
                            fields={"to_id": "beat-adventure-complete"})],
        )
        result = C.first_transition_is_lowest_order("beat-entry-hall")(turn)
        assert result.status == "FAIL"
        assert "skipped ahead" in result.detail

    async def test_first_transition_check_skips_on_later_turns(self, harness_client, sink):
        # It must not fire on a normal mid-story advance, or it would fail every
        # legitimate transition after the first.
        runner = PlaytestRunner(harness_client, sink)
        script = PlaytestScript(
            name="skip-later",
            description="Advance twice; only the first transition is judged.",
            steps=[
                Step(jump_to="beat-entry-hall", label="jump to the entry hall"),
                Step(
                    say="We press on.",
                    label="press on",
                    beat_advance=True,
                    expect=[C.first_transition_is_lowest_order("beat-entry-hall")],
                ),
            ],
        )

        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            turns = await runner.run_script(script)

        assert turns[1].beat_after == "beat-trapped-corridor"
        assert runner.failures == []
        statuses = {c.name: c.status for c in turns[1].checks}
        assert statuses["first_transition_is_lowest_order"] == "SKIP"

    async def test_a_failing_check_is_recorded_not_raised(self, harness_client, sink):
        runner = PlaytestRunner(harness_client, sink)
        script = PlaytestScript(
            name="failing",
            description="A check that cannot pass.",
            steps=[
                Step(
                    say="I look around.",
                    label="look",
                    expect=[C.beat_is("beat-lich-revealed")],
                ),
                Step(say="I keep looking.", label="look again", expect=[C.no_error()]),
            ],
        )

        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            turns = await runner.run_script(script)

        # The run continues past the failure so the transcript stays complete.
        assert len(turns) == 2
        assert len(runner.failures) == 1
        assert runner.failures[0].name == "beat_is"
        assert runner.failures[0].step == 1


class TestOptionDrivenRuns:
    """The model's own options drive the story; no authored player intent."""

    async def test_always_first_takes_slot_one_every_turn(self, harness_client, sink):
        runner = PlaytestRunner(harness_client, sink)
        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            script, turns = await runner.run_option_driven(ALWAYS_FIRST, max_turns=3)

        assert len(turns) == 3
        assert [t.chosen_index for t in turns] == [0, 0, 0]
        # The mocked option 0 is "Wait." — it should be what was actually sent.
        assert all(t.user_message == "Wait." for t in turns)
        assert runner.stop_reason == "hit max_turns (3)"

    async def test_option_click_maps_to_the_request_like_the_ui_does(
        self, harness_client, sink
    ):
        # Game.tsx does send(opt.text, opt.advances_beat, opt.dice_roll); the
        # harness must send the same three things or the run isn't representative.
        always_third = OptionPolicy(
            name="always-third", description="pick the dice option", pick=lambda o, r: 2
        )
        runner = PlaytestRunner(harness_client, sink)
        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            _, turns = await runner.run_option_driven(always_third, max_turns=1)

        chosen = turns[0].chosen_option
        assert chosen["text"] == "Lift the flagstone."
        assert turns[0].request["user_message"] == chosen["text"]
        assert turns[0].request["beat_advance"] == chosen["advances_beat"]
        assert turns[0].request["dice_roll"] == chosen["dice_roll"]
        assert turns[0].roll is not None, "the option's dice spec should have rolled"

    async def test_random_is_reproducible_from_its_seed(self, harness_client, sink):
        picks = []
        for _ in range(2):
            runner = PlaytestRunner(harness_client, sink)
            structured, stream = _mocked_llm(_director_raw("bram"))
            with structured, stream:
                _, turns = await runner.run_option_driven(
                    RANDOM_CHOICE, max_turns=5, seed=99
                )
            picks.append([t.chosen_index for t in turns])
        assert picks[0] == picks[1], f"same seed gave different walks: {picks}"

    async def test_a_different_seed_gives_a_different_walk(self, harness_client, sink):
        runs = []
        for seed in (1, 2, 3):
            runner = PlaytestRunner(harness_client, sink)
            structured, stream = _mocked_llm(_director_raw("bram"))
            with structured, stream:
                _, turns = await runner.run_option_driven(
                    RANDOM_CHOICE, max_turns=6, seed=seed
                )
            runs.append(tuple(t.chosen_index for t in turns))
        assert len(set(runs)) > 1, f"all seeds produced the same walk: {runs}"

    async def test_run_stops_at_the_ending(self, harness_client, sink):
        # Sitting on the final beat, taking the advancing option fires the
        # ending — the loop must stop rather than keep clicking into a finished
        # story. Mocked option 1 is the one flagged advances_beat.
        take_advancing = OptionPolicy(
            name="take-advancing", description="pick the flagged option",
            pick=lambda o, r: 1,
        )
        runner = PlaytestRunner(harness_client, sink)

        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            await runner.create_save()
            save = runner.get_save()
            save.current_beat_id = "beat-adventure-complete"
            storage.save_save(save)
            await runner.seed_options()

            script, turns = await runner.run_option_driven(
                take_advancing, max_turns=10
            )

        assert runner.stop_reason == "reached the ending"
        assert turns[-1].ending_reached
        assert len(turns) == 1, "should stop on the first turn that ends the story"

    async def test_progression_counters_answer_did_it_get_anywhere(
        self, harness_client, sink
    ):
        runner = PlaytestRunner(harness_client, sink)
        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            script, turns = await runner.run_option_driven(ALWAYS_FIRST, max_turns=3)

        counters = build_report(script, turns, {"model": "mock"})["counters"]
        picks = counters["option_picks"]
        assert picks["turns"] == 3
        assert picks["index_histogram"] == {"0": 3}
        # Mocked option 0 is not the advancing one; option 1 is.
        assert picks["picked_an_advancing_option"] == 0
        assert picks["turns_offering_an_advancing_option"] == 3

        prog = counters["progression"]
        assert prog["turns_without_progress"] == 3, "no beat should have moved"
        assert prog["beats_reached_in_order"] == ["none"]

    async def test_script_is_built_from_what_was_actually_chosen(
        self, harness_client, sink, tmp_path
    ):
        # The steps aren't known up front, so the runner assembles them — the
        # report and transcript need them to reflect the real choices.
        runner = PlaytestRunner(harness_client, sink)
        structured, stream = _mocked_llm(_director_raw("bram"))
        with structured, stream:
            script, turns = await runner.run_option_driven(ALWAYS_FIRST, max_turns=2)

        assert script.name == "always-first"
        assert len(script.steps) == 2
        assert script.steps[0].say == "Wait."
        assert script.steps[0].label.startswith("[1/4]")

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        write_run(run_dir, script, turns, {"model": "mock", "seed": 1})
        transcript = (run_dir / "transcript.md").read_text(encoding="utf-8")
        assert "Chose option 1 of 4" in transcript
        assert "**→** Wait." in transcript


class TestReportArtifacts:

    async def test_write_run_produces_all_five_artifacts(self, harness_client, sink, tmp_path):
        runner = PlaytestRunner(harness_client, sink)
        script = PlaytestScript(
            name="artifacts",
            description="One turn, then write the run out.",
            steps=[Step(say="I look around.", label="look", expect=[C.no_error()])],
        )

        structured, stream = _mocked_llm(
            _director_raw("bram"), narration="Dust motes hang in the torchlight."
        )
        with structured, stream:
            turns = await runner.run_script(script)

        meta = {"model": "mock", "started": "now", "save_id": runner.save_id, "duration_s": 1.0}
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        write_run(run_dir, script, turns, meta, save_json='{"id": "x"}')

        for name in ("transcript.md", "events.jsonl", "markers.jsonl", "report.json", "save.json"):
            assert (run_dir / name).exists(), f"{name} was not written"

        transcript = (run_dir / "transcript.md").read_text(encoding="utf-8")
        assert "All structural checks passed." in transcript
        assert "I look around." in transcript
        assert "Dust motes hang in the torchlight." in transcript
        assert "**Options offered:**" in transcript

        markers_lines = (run_dir / "markers.jsonl").read_text(encoding="utf-8").splitlines()
        assert markers_lines, "no markers recorded"
        assert all(json.loads(line)["step"] == 1 for line in markers_lines)

    async def test_report_counters_summarize_the_run(self, harness_client, sink):
        runner = PlaytestRunner(harness_client, sink)
        script = PlaytestScript(
            name="counters",
            description="One advancing turn.",
            steps=[Step(say="We press on.", label="press on", beat_advance=True)],
        )

        structured, stream = _mocked_llm(
            _director_raw("bram", beat_transition=True, next_beat_id="beat-entry-hall")
        )
        with structured, stream:
            turns = await runner.run_script(script)

        report = build_report(script, turns, {"model": "mock"})
        counters = report["counters"]
        assert counters["turns"] == 1
        assert counters["beat_advances"] == 1
        assert counters["fallbacks"] == 0
        assert counters["errors"] == 0
        assert counters["blank_messages"] == 0
        assert report["turns"][0]["beat_after"] == "beat-entry-hall"
