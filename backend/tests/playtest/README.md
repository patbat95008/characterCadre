# Playtest harness

Programmatic replacement for typing the same three playthroughs into the browser
by hand. Drives real `/api/chat/turn` calls against the real local LLM, records
everything the model said, and hard-asserts only the facts a model cannot make
ambiguous.

## Running

Live tests are excluded from the default run (`addopts = -m 'not live'`), so the
normal suite stays fast and offline:

```bash
python -m pytest tests/ -q
```

Start with the smoke test — one real turn, ~30s. It proves the path works before
you spend ten minutes finding out it doesn't:

```bash
python -m pytest tests/playtest -m live -q -k smoke -s
```

Then a full script, or all three:

```bash
python -m pytest tests/playtest -m live -s -k dutiful
```

Add `CC_DEBUG=1` (PowerShell: `$env:CC_DEBUG='1'`) to also capture every prompt
and response into the run directory. Each entry is tagged `turn=<id> · <phase>`,
so a specific turn's prompts can be found in the dump.

If Ollama isn't running, or the model isn't resident, the tests **skip** with a
message rather than failing on a timeout. Warm the model first — `launch.py`
does it, or:

```bash
curl http://localhost:11434/api/generate -d '{"model":"mistral-small3.1","keep_alive":-1}'
```

Budget roughly 7 LLM calls and 20–30s per turn.

## Output

Each run writes to `runs/<utc-timestamp>-<script>/` (gitignored):

| File | What it's for |
|---|---|
| `transcript.md` | **The thing you read.** Every word the LLM produced, in order, with the player's input, the dice, the options and their flags, and the checks/probes that fired on each turn. |
| `report.json` | Stable schema for diffing runs. Counters (fallbacks, regenerates, blank messages, beats visited) plus every check result. |
| `events.jsonl` | Every SSE event, tagged with its turn. |
| `markers.jsonl` | Every structured marker, tagged with its turn. |
| `save.json` | The final save — the full persisted message log. |
| `ollama-*.log` | Prompt/response dumps, when run with `CC_DEBUG=1`. |

## How it decides pass/fail

**Checks** (`checks.py`) are structural and hard. A FAIL means the *program* did
something wrong — the beat advanced when no transition condition was satisfied, a
blank message got persisted, a fallback engaged, the streamed text didn't match
what was saved. Failures are collected, not raised, so a run that breaks at step
3 still produces a full transcript through step 11.

**Probes** (`probes.py`) are soft keyword signals over the prose and never fail a
run. They exist so the report can say "the DM conjured a barkeep on step 3"
without a human re-reading 4,000 words to find it. The transcript is the
evidence; the probe is the index.

A probe hit is not a defect. `improvised_location` in particular fires when the
DM builds scenery the world data doesn't contain — which is the app working as
intended when a player walks off the rails. What it buys you is a cursor into
the transcript, so improvised detail can be checked for continuity across turns.

Nothing judges whether the prose is *good*. That stays with the human.

## The three scripts

| Script | Theme | What it's really testing |
|---|---|---|
| `dutiful` | Follow the pre-scripted adventure | All five beats to `ending_reached`, using the beat-advance toggle where the story is meant to move. |
| `tavern-derail` | Leave the adventure, go drinking | **There is no tavern in the world data** — no inn, no settlement, no barkeep. Improvising one is the point; what's asserted is that the *plot* holds still while it happens, so no beat may advance while the player is off-script. |
| `trap-and-surrender` | Trigger the trap, surrender to the boss | **Surrender is not a scripted outcome.** Beat 3 ends only when the Lich King is defeated *and* the Ember Clasp is taken, so this asserts the Director doesn't rubber-stamp an advance. Also runs the direct `((OOC: Advance to the next scene please.))` request with the flag deliberately unset, to see whether free text alone moves the story. |

Plus two option-driven modes with no authored intent — see below.

## Option-driven runs

Two runs supply **no authored player intent at all** — the story is driven
entirely by the options the model generated for itself. They probe Phase 3
directly: do its suggestions actually go somewhere, or do they circle?

| Mode | Policy |
|---|---|
| `always-first` | Take slot one every turn. The options instruction asks for "one cautious, one bold, one curious, one witty" in no fixed order, so slot one is not deliberately the forward move. |
| `random-choice` | Uniformly random. The RNG is seeded and the seed is written to `report.json`, so an interesting walk can be replayed exactly. |

```bash
python -m pytest tests/playtest -m live -s -k always_first
```

Clicking an option in the UI sends its text, its `advances_beat` as the
beat-advance flag, and its `dice_roll` (`Game.tsx`:
`send(opt.text, opt.advances_beat, opt.dice_roll)`). The harness mirrors that
exactly, so a run reflects what a real player clicking through would get.

A run stops on `ending_reached`, on a turn error, when no options come back, or
at `max_turns` (default 20). The stop reason is recorded in `meta`.

**These measure rather than assert.** "It wandered for 20 turns and never left
the first beat" is a *result*, not a test failure — only turn-level breakage
(an error, an empty turn, malformed options) fails them. `report.json` grows two
extra blocks:

- `option_picks` — which slots were taken, how often an advancing option was
  *offered* versus *taken*, how often a dice option was taken
- `progression` — the beat path in order, turns per beat, turns where nothing
  moved

To start a run from a prepared state rather than the scenario opening, set up
the save first — `run_option_driven` only creates one if you haven't:

```python
await runner.create_save()
save = runner.get_save(); save.current_beat_id = "beat-the-battle"
storage.save_save(save)
await runner.seed_options()
script, turns = await runner.run_option_driven(ALWAYS_FIRST, max_turns=10)
```

## Adding a script

Scripts are plain data in `scripts.py`:

```python
Step(
    say="I wedge my dagger under the raised flagstone.",
    label="clear the pressure plate",
    beat_advance=True,          # the yellow toggle
    use_offered_dice=True,      # replay the dice_roll the model attached to an option
    retry_on_dice_failure=2,    # dice are unseeded; re-run rather than hostage a scripted beat
    expect=_baseline() + [C.advance_matches_dice_outcome()],
    probes=_baseline_probes(),
)
```

`Step(jump_to="beat-the-battle")` advances via the manual endpoint instead of a
turn, to reach a set piece without spending LLM time on the run-up.

Write player lines that hook the fixtures — Bram's "we stay together", Silvaine's
House Ardenveil boast, the bronze clasp on the cape. Generic prompts tell you
almost nothing about whether the personas are working.

## Markers

The harness reads structured markers the app emits on the `cc.marker` logger:

```
CCM|beat.advance|turn=a1b2c3d4|to_id=beat-trapped-corridor|trigger=player
```

They carry what SSE doesn't: the beat-advance **trigger**, whether a fallback
engaged, why an advance was **blocked**, the speaker plan, and per-call latency.
`app/markers.py` holds both `emit()` and `parse()` so the format has one
definition. `CC_MARKERS=0` keeps them out of the console without stopping the
harness from capturing them.

The marker contract is tested offline in `tests/integration/test_chat_turn.py`
(`TestTurnMarkers`) and the harness plumbing in
`tests/integration/test_playtest_harness.py` — so a live failure means the model
misbehaved, not the instrumentation.
