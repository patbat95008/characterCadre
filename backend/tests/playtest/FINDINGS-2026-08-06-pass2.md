# Playtest findings — 2026-08-06, pass 2

Second pass, after acting on `FINDINGS-2026-08-06.md`. Same model
(`mistral-small3.1`), same in-process turn endpoint, no mocks. All four runs
single-threaded, one at a time.

| Script | Turns | Wall | LLM calls | PASS | WARN | FAIL |
|---|---:|---:|---:|---:|---:|---:|
| `tavern-derail` | 6 | 132s | 34 | 49 | 0 | 0 |
| `dutiful` | 12 | 302s | 76 | 102 | 0 | 0 |
| `trap-and-surrender` | 9 | 130s | 33 | 45 | 0 | 0 |
| `always-first` | 7 | 167s | 43 | 42 | 0 | 0 |

**Zero WARN, zero FAIL across all four.** Previous pass: 5 FAIL, 15 WARN.

Transcripts under `runs/20260806-23*`.

---

## The bug the first pass missed

Finding 8 blamed the `always-first` stall on tone ordering. Tone ordering was
real but secondary. The actual cause was an off-by-one in `run_phase3`:

```python
next_beat = find_next_beat(save, scenario)
transition_condition = next_beat.transition_condition if next_beat else None
```

`find_next_beat` returns the beat you would move *into*. A beat's
`transition_condition` is what moves you *out of* it. So on `beat-entry-hall` the
option generator was told the gate was *"When the party gets past the fire trap"*
— a trap two rooms away — while the Director was correctly judging against
*"When the party moves past the guardroom."* **The two halves of the system were
reading different rows of the beat table on every single turn.**

That is why forward-looking options were never the ones that satisfied the real
gate, and why the DM eventually stopped offering them at all: it was being asked
to flag options against a condition the scene could not possibly satisfy.

Fixed by separating the two questions that were conflated:

- *What would an advance even do here?* → `phases.classify_advance()`
- *What would satisfy it?* → the **current** beat's `transition_condition`

`classify_advance` returns `start` / `beat` / `ending` / `none`, mirroring the
player-flag branch in `routes/chat.py` exactly.

### A related correction to finding 8's premise

The first pass called the two terminal-beat `advance_flag_is_actionable` warnings
"dead buttons". They are not — on the final beat the flag is what fires the
**ending** (`chat.py:244-248`). An early version of this fix stripped
`advances_beat` whenever `find_next_beat` returned `None`, which silently
disabled the ending; `test_run_stops_at_the_ending` caught it. The flag is only
genuinely inert in sandbox mode and beatless scenarios, which is what
`advance_kind="none"` now means.

---

## `always-first`: the headline result

The mode the first pass called "a permanently cautious game that never moves".

| | Before | After |
|---|---|---:|
| Turns | 20 (cap) | **7** |
| Stop reason | hit max_turns | **reached the ending** |
| Furthest beat | `beat-entry-hall` | `beat-adventure-complete` |
| Beats visited | 1 of 5 | **5 of 5** |
| Turns with no progress | 19 / 20 | **2 / 7** |
| Advancing option offered | 2 / 20 | **6 / 7** |
| Advancing option taken | 1 | **6** |
| Dice option taken | 0 | 3 |
| LLM calls | 120 | 43 |

Taking the model's own top suggestion every turn now walks the whole adventure.

**Pacing note, for a product decision:** seven turns is fast. The advancing
option lands in slot 0 on nearly every turn now, so a player who habitually
clicks the top option finishes the Ironroot Dungeon in about seven exchanges.
That is the requested behaviour working exactly as specified, but it is worth
deciding whether the *offer rate* wants tuning — currently the DM offers a
forward option on 6 of 7 turns, where something closer to every other turn would
give scenes room to breathe.

---

## Findings from pass 1: status

| # | Finding | Status |
|---|---|---|
| 1 | Null `current_beat_id` stalled then skipped | fixed in pass 1, holds |
| 2 | `beat-the-battle` commits a blank message | **fixed** |
| 3 | Director advances beats nobody asked it to | **fixed** |
| 4 | Bare OOC request bypasses the transition condition | **fixed, incidentally** |
| 5 | The companions barely exist | **partly fixed** — see below |
| 6 | On the terminal beat the Director invents beat IDs | **fixed** |
| 7 | Persona drift in Bram | **fixed** |
| 8 | Option slot order encodes tone | **fixed**, plus the real cause above |

### 2 — blank message

Two guards, as recommended. `apply_beat_transition` skips the append when the
starter is whitespace-only, and `_BEAT_THE_BATTLE` got a real starter (503 chars,
the Lich King's opening proclamation with the Ember Clasp visible on his cape).
The data file `data/scenarios/ironroot-dungeon.json` needed the same edit — the
seeder only runs on an empty data dir, so `fixtures.py` alone changes nothing in
a running app. `blank_messages` 1 → 0.

### 3 and 4 — transition conditions read as proximity

One consolidated Director rule replaced the old item 2:

> It is a completion test, not a description of where the party roughly is — the
> event it names must have actually happened. Heading toward it, being near it,
> or talking about it is `beat_transition=false`, as is having no beat listed
> after the current one.

`trap-and-surrender` step 3, where the player asked *"Gods. Bram — are you
burning?"* and the story jumped a beat, now holds still. So does step 8:

```
 8  direct OOC advance request (no flag)   beat-the-battle -> beat-the-battle
```

Probe: `ooc_honored: NOT honored — free-text OOC did not advance the beat`.

Worth being explicit that **no OOC-specific rule was written** — the decision was
to leave OOC policy alone. The hole closed as a side effect of the completion-test
rule, because "please advance the scene" is not the Lich King being defeated. The
guard that correctly refused a surrender now also refuses a polite request, and
the ending on step 9 comes from the player flag, as designed.

### 5 — companions

Bram improved clearly. Silvaine did not.

| Run | Bram | Silvaine |
|---|---|---|
| `dutiful` | 2/11 → **7/12** | 2/11 → 1/12 |
| `tavern-derail` | 0/6 → **2/6** | 0/6 → **0/6** |
| `trap-and-surrender` | 1/9 → 1/9 | 0/9 → 1/9 |
| `always-first` | 8/20 → 2/7 (rate 40% → 29%) | 2/20 → 2/7 (10% → 29%) |

`director_declined_speaker` on `dutiful` fell 7 → 4, and on `always-first`
10 → 2, so the Director is nominating someone far more often. But Silvaine is
still the one it doesn't nominate — six turns of `tavern-derail` with nothing
from her again.

Three changes went in: a hard rule that a companion addressed by name gets the
speaker slot, a factual staleness line (`Turns since each companion last spoke —
Bram Stonefist: 4, Silvaine Dawnwhisper: 9`), and a tightened DM exclusion note
that no longer licenses narrating companion actions.

The staleness line is clearly not enough on its own for Silvaine. The next lever,
if this matters, is the validator retry that was deliberately skipped this pass:
reject a `speaker_character_id=null` when the player's message names exactly one
companion. It costs one extra structured call on those turns.

### 6 — invented beat IDs on the terminal beat

Prompt plus validator. The roster footer now says outright *"This is the final
beat. There is no next beat and none can be invented — set beat_transition=false"*,
and `validate_director_response` clamps a terminal-beat transition to `Ok` with
the transition dropped rather than returning `Err`. Rejecting burned all three
attempts and landed on `DIRECTOR_FALLBACK`; that was the entire source of the
previous pass's 8 validation failures and both fallbacks.

`validation_failures` on `dutiful` 2 → 0, `fallbacks` 0 → 0.

The clamp is narrow on purpose: an invented id anywhere a forward beat exists is
still rejected, covered by
`test_invented_beat_id_still_rejected_when_a_forward_beat_exists`.

### 7 — Bram

Name prefixes and stage directions were structural rather than per-character, so
one shared rule went into `build_character_prompt` for every non-DM speaker
rather than into each card. `lad`/`lass` was dropped from Bram's card in favour of
addressing the player by name — deterministic, and it cannot misgender anyone.

- `name_prefix_leak`: 4 hits before (2 in `dutiful`, 2 in `always-first`) → **0
  across all four runs**.
- `lad` / `lass`: **0 occurrences** anywhere in the four transcripts.

Bram's first line of the smoke run: *"It stinks in here, Kestrel. Old magic, bad
air — and something worse."*

---

## Still true, still worth stating

- `weakness_leak`: **0 hits** across all four runs. The Lich King's secret has
  now survived two full passes.
- 0 errors, 0 loops, 0 regenerations, 0 empty speaker plans, 0 fallbacks.
- Option discipline exact: every option turn returned 4, never more than one
  `advances_beat`, never more than one `dice_roll`.
- `tavern-derail` still improvises freely and **still does not move the plot** —
  zero `beat.advance` markers across six off-script turns. This was the
  regression most at risk from making options lean forward, and it held.

---

## Token cost

The Director's closing instruction went 279 → 377 tokens on a beat-carrying turn,
plus 26 for the staleness line: **+124 tokens per Director call**. Speaker calls
gain 32 tokens for the shared dialogue-only rule.

Partly offset by making the beat rule conditional — sandbox and beatless
scenarios now get 271 tokens instead of 279, since there is no point spending ~90
tokens telling the model to reason about a roster it was not sent.

Cheaper in practice regardless: `always-first` went from 120 LLM calls to 43 for
a strictly better outcome.

---

## Method notes

- All runs single-threaded, one pytest invocation at a time. No `pytest-xdist`
  installed; do not add `-n`.
- Baselines used for the diffs: `runs/20260806-205420-dutiful` (the post-fix
  rerun, not the original), `runs/20260806-221903-always-first`,
  `runs/20260806-203414-tavern-derail`, `runs/20260806-204758-trap-and-surrender`.
- `runs/20260806-203414-tavern-derail` predates the `speaking_turns` counter, so
  its companion numbers were recomputed from `turns[].messages`.
- Offline suite: 304 passed. `test_list_saves_sorted_by_updated_at` is
  intermittently flaky on timestamp collisions — pre-existing, fails on the
  unmodified tree too, unrelated to this work.
