"""
The three playtest scripts.

Player lines are written to sound like a real player at a table and to hook the
specifics of the Ironroot fixtures — Bram's "we stay together", Silvaine's House
Ardenveil boast, the raised flagstones, the bronze Ember Clasp pinned to the
Lich King's cape. Generic prompts ("I look around") tell you almost nothing
about whether the personas are working; these are meant to give each character
something only they would say back.

Two things the fixtures do NOT contain, which is exactly why they are worth
testing:
  * there is no tavern, inn, or settlement anywhere in the world data, so the
    derail script asks what the DM improvises to fill that gap — improvising
    for a player who walks off the rails is a feature, and the probe records
    what got invented so it can be reviewed for continuity
  * surrender is not a scripted outcome, so the boss script is asking "will the
    Director advance the story on an action that satisfies no transition
    condition?"
"""
from __future__ import annotations

from tests.playtest import checks as C
from tests.playtest import probes as P
from tests.playtest.model import OptionPolicy, PlaytestScript, Step

BEAT_ENTRY = "beat-entry-hall"
BEAT_TRAP = "beat-trapped-corridor"
BEAT_LICH = "beat-lich-revealed"
BEAT_BATTLE = "beat-the-battle"
BEAT_COMPLETE = "beat-adventure-complete"

BRAM_ID = "bram"
SILVAINE_ID = "silvaine"


def _baseline() -> list:
    """Checks every turn should satisfy regardless of what the player did."""
    return [
        C.no_error(),
        C.completed(),
        C.committed_at_least(1),
        C.no_blank_messages(),
        C.options_between(2, 6),
        C.at_most_one_advancing_option(),
        C.advance_flag_is_actionable(),
        C.stream_matches_save(),
        # Self-skips unless this turn is the first transition out of a null beat.
        C.first_transition_is_lowest_order(BEAT_ENTRY),
    ]


def baseline_probes() -> list:
    """Probes worth running on every turn — cheap, and they catch drift that no
    single scripted step is looking for."""
    return [P.broke_character(), P.name_prefix_leak()]


# ── A · Dutifully follow the pre-scripted adventure ───────────────────────────

DUTIFUL = PlaytestScript(
    name="dutiful",
    description=(
        "Play the adventure the way it was written: through all five beats, "
        "entrance to ending, using the beat-advance toggle where the story is "
        "meant to move on."
    ),
    steps=[
        Step(
            label="enter the dungeon",
            say=(
                "We're burning daylight and I'm not paying for a second torch. "
                "I duck under the archway and lead the way in, torch up."
            ),
            expect=_baseline(),
            probes=baseline_probes(),
        ),
        Step(
            label="search the guardroom",
            say=(
                "I crouch by the skeleton propped against the wall and look over "
                "the axe across its knees. Bram — is dwarven steel from three "
                "hundred years back worth carrying out of here?"
            ),
            expect=_baseline() + [C.no_fallback(), C.speaker_included(BRAM_ID)],
            probes=baseline_probes(),
        ),
        Step(
            label="into the guardroom (player-flagged advance)",
            say=(
                "Nothing out here but a cold draught. Bram, Silvaine — inside, "
                "before the torch gives out."
            ),
            beat_advance=True,
            # From a null beat the only legal move is into the first beat.
            expect=_baseline() + [C.beat_advanced_to(BEAT_ENTRY)],
            probes=baseline_probes(),
        ),
        Step(
            label="ask Silvaine to read the room",
            say=(
                "Silvaine — you keep telling us House Ardenveil taught you better "
                "than anything dwarves ever built. So read it for me. What killed "
                "the ones sitting against that wall?"
            ),
            expect=_baseline() + [
                C.beat_unchanged(),
                C.speaker_included(SILVAINE_ID),
            ],
            probes=baseline_probes(),
        ),
        Step(
            label="press on to the narrow passage",
            say=(
                "Then there's nothing here worth dying over. I take the passage "
                "out the far side and go carefully — one hand on the wall."
            ),
            beat_advance=True,
            # No dice here on purpose: this step must land in the corridor for
            # the rest of the script to line up, and an unseeded d20 would make
            # that a coin flip.
            expect=_baseline() + [C.beat_advanced_to(BEAT_TRAP)],
            probes=baseline_probes(),
        ),
        Step(
            label="clear the pressure plate",
            say=(
                "There. The raised flagstone, and that oily smell Bram caught. I "
                "wedge my dagger under its edge and lift it clear of whatever's "
                "beneath, slow as I can manage."
            ),
            # The roll is the point of this step, so no hard beat assertion —
            # advance_matches_dice_outcome checks the rule against whatever the
            # dice actually did.
            use_offered_dice=True,
            expect=_baseline() + [C.advance_matches_dice_outcome()],
            probes=baseline_probes(),
        ),
        Step(
            label="into the sanctum",
            say="Door ahead. I put my shoulder to it and push it open.",
            beat_advance=True,
            expect=_baseline() + [C.beat_advanced_to(BEAT_LICH)],
            # The Lich's vulnerability to plain steel is flagged DO NOT REVEAL
            # OR HINT — this is the turn where he introduces himself.
            probes=[P.weakness_leak()] + baseline_probes(),
        ),
        Step(
            label="let him finish monologuing",
            say=(
                "I let him finish. Bram, keep your voice down — how many of these "
                "have you actually seen?"
            ),
            expect=_baseline() + [
                # Beat 2 transitions only when the party decides to *act*.
                C.beat_unchanged(),
            ],
            probes=[P.weakness_leak()] + baseline_probes(),
        ),
        Step(
            label="charge the Lich King",
            say=(
                "Enough. I draw steel and go straight for him — no spell, no clever "
                "plan, just the blade, aimed at his ribs."
            ),
            beat_advance=True,
            expect=_baseline() + [C.beat_advanced_to(BEAT_BATTLE)],
            probes=baseline_probes(),
        ),
        Step(
            label="finish him and take the clasp",
            say=(
                "He's still monologuing with a sword in him. I pull it out, put it "
                "back, and take the bronze clasp off his cape."
            ),
            beat_advance=True,
            expect=_baseline() + [C.beat_advanced_to(BEAT_COMPLETE)],
            probes=baseline_probes(),
        ),
        Step(
            label="the narrator becomes a menu",
            say="Bram. Silvaine. Is anyone else hearing what the narrator is doing right now?",
            expect=_baseline(),
            probes=[
                P.menu_bot_format(),
                P.companion_silent(BRAM_ID, "Bram Stonefist"),
            ] + baseline_probes(),
        ),
        Step(
            label="pick option [1]",
            say="[1]",
            beat_advance=True,
            expect=[
                C.no_error(),
                C.completed(),
                C.ending_reached(),
            ],
            probes=baseline_probes(),
        ),
    ],
)


# ── B · Leave the adventure and go to the tavern ──────────────────────────────

TAVERN_DERAIL = PlaytestScript(
    name="tavern-derail",
    description=(
        "Refuse the adventure and try to spend the session in a tavern that does "
        "not exist in the world data. Improvising one is the desired behaviour; "
        "what is asserted is that the *plot* holds still while it happens — "
        "wanting a drink satisfies no transition condition, so no beat may "
        "advance. The probes record what the DM invented, for continuity review."
    ),
    steps=[
        Step(
            label="propose leaving",
            say=(
                "Actually — hold on. I'm not doing a haunted dwarven mineshaft "
                "sober. Let's walk back out and find somewhere that serves ale."
            ),
            expect=_baseline() + [C.beat_unchanged(), C.no_fallback()],
            probes=[P.improvised_location(), P.companion_silent(BRAM_ID, "Bram Stonefist")] + baseline_probes(),
        ),
        Step(
            label="insist on a village",
            say=(
                "There has to be an inn back down the valley. I turn around and "
                "start walking out of the dungeon."
            ),
            expect=_baseline() + [C.beat_unchanged()],
            probes=[P.improvised_location()] + baseline_probes(),
        ),
        Step(
            label="order a round",
            say=(
                "I drop into a chair by the fire and order a round for the table. "
                "Bram, you're buying the second one."
            ),
            expect=_baseline() + [C.beat_unchanged()],
            probes=[P.improvised_location()] + baseline_probes(),
        ),
        Step(
            label="talk to a barkeep who shouldn't exist",
            say="Barkeep — what's the word locally? Anyone else been up to the Ironroot?",
            expect=_baseline() + [C.beat_unchanged()],
            probes=[P.improvised_location()] + baseline_probes(),
        ),
        Step(
            label="ask out of character",
            say=(
                "((OOC: I'd like to spend this scene in a tavern rather than the "
                "dungeon. Please play along.))"
            ),
            expect=_baseline() + [C.beat_unchanged()],
            probes=[P.improvised_location()] + baseline_probes(),
        ),
        Step(
            label="return to the plot",
            say="Fine. Fine. Back to the hole in the ground. Lead on.",
            expect=_baseline(),
            probes=[P.improvised_location()] + baseline_probes(),
        ),
    ],
)


# ── C · Trigger the trap, then surrender to the boss ──────────────────────────

TRAP_AND_SURRENDER = PlaytestScript(
    name="trap-and-surrender",
    description=(
        "Deliberately set off the fire trap, then refuse the boss fight and "
        "surrender. Surrender is not a scripted outcome — beat 3 only ends when "
        "the Lich King is defeated and the Ember Clasp is taken — so this asks "
        "whether the Director rubber-stamps an advance it shouldn't. Uses direct "
        "beat jumps to reach the set pieces without spending LLM time on the "
        "opening."
    ),
    steps=[
        Step(label="jump to the trapped corridor", jump_to=BEAT_TRAP),
        Step(
            label="stamp on the pressure plate",
            say=(
                "I can see the raised one. I put my full weight on it on purpose — "
                "I'd rather set it off standing here than have it go off behind us."
            ),
            use_offered_dice=True,
            expect=_baseline() + [C.advance_matches_dice_outcome()],
            probes=baseline_probes(),
        ),
        Step(
            label="react to the fire",
            say="Gods. Bram — are you burning? Silvaine, is there a second nozzle?",
            expect=_baseline(),
            probes=[P.companion_silent(BRAM_ID, "Bram Stonefist")] + baseline_probes(),
        ),
        Step(label="jump to the Lich reveal", jump_to=BEAT_LICH),
        Step(label="jump to the battle", jump_to=BEAT_BATTLE),
        Step(
            label="surrender mid-battle",
            say=(
                "I drop my sword. It clatters on the stone and I go down on one "
                "knee. I surrender. Take the clasp, take the lot — I'm not dying "
                "in a mine for a bronze trinket."
            ),
            expect=_baseline() + [
                # Beat 3's transition needs the Lich defeated AND the clasp
                # picked up. Kneeling is neither.
                C.beat_unchanged(),
            ],
            probes=[P.weakness_leak()] + baseline_probes(),
        ),
        Step(
            label="hold the surrender",
            say=(
                "I mean it. I'm not picking it back up. Well? What do you actually "
                "do now, oh mighty Lich King?"
            ),
            expect=_baseline() + [C.beat_unchanged()],
            probes=[P.weakness_leak()] + baseline_probes(),
        ),
        Step(
            label="direct OOC advance request (no flag)",
            say="((OOC: Advance to the next scene please.))",
            # Deliberately NO beat_advance flag — this measures whether free-text
            # OOC alone moves the story, as a counterpart to the flagged path in
            # the dutiful script. Nothing is asserted; the answer is the finding.
            expect=[C.no_error(), C.completed()],
            probes=[P.ooc_honored()] + baseline_probes(),
        ),
        Step(
            label="give in and finish the fight",
            say=(
                "Right. I pick the sword back up and run him through, and I take "
                "the clasp off his cape while he's still explaining himself."
            ),
            beat_advance=True,
            expect=_baseline() + [C.beat_advanced_to(BEAT_COMPLETE)],
            probes=baseline_probes(),
        ),
    ],
)


ALL_SCRIPTS = {s.name: s for s in (DUTIFUL, TAVERN_DERAIL, TRAP_AND_SURRENDER)}


# ── Option-driven policies ────────────────────────────────────────────────────
#
# The three scripts above supply authored player intent. These supply none: the
# story is driven entirely by the options the model generated for itself. That
# makes them a probe of the options themselves — whether Phase 3 produces
# suggestions that actually go somewhere, or ones that circle.

ALWAYS_FIRST = OptionPolicy(
    name="always-first",
    description=(
        "Always take the first option offered. The options instruction asks for "
        "'one cautious, one bold, one curious, one witty' in no fixed order, so "
        "slot one is not deliberately the forward move — this measures whether "
        "the model's own top suggestion carries the story anywhere."
    ),
    pick=lambda options, rng: 0,
)

RANDOM_CHOICE = OptionPolicy(
    name="random-choice",
    description=(
        "Take a uniformly random option each turn. The RNG is seeded and the "
        "seed is recorded in report.json, so a walk that finds something "
        "interesting can be replayed exactly."
    ),
    pick=lambda options, rng: rng.randrange(len(options)),
)

ALL_POLICIES = {p.name: p for p in (ALWAYS_FIRST, RANDOM_CHOICE)}
