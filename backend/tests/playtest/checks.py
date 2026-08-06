"""
Structural checks — the machine-verifiable half of a playtest.

Nothing here looks at whether the prose is *good*; that judgement stays with the
human reading the transcript. These assert only facts the LLM cannot make
ambiguous: which beat we ended on, whether a fallback engaged, whether an
advance the player asked for actually happened, whether anything blank got
committed.

Every check returns a CheckResult rather than raising, so one failure doesn't
abort the run — a playthrough that fails at step 3 still produces a full
transcript through step 11.
"""
from __future__ import annotations

from app.dice import outcome_is_success
from tests.playtest.model import Check, CheckResult, TurnResult


def _ok(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name=name, status="PASS", detail=detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="FAIL", detail=detail)


def _warn(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="WARN", detail=detail)


def _skip(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="SKIP", detail=detail)


# ── Turn health ───────────────────────────────────────────────────────────────

def no_error() -> Check:
    def check(t: TurnResult) -> CheckResult:
        if t.error:
            return _fail("no_error", f"turn emitted error: {t.error}")
        return _ok("no_error")
    return check


def completed() -> Check:
    """The stream ran to `done`, not cut short."""
    def check(t: TurnResult) -> CheckResult:
        if t.events_named("done"):
            return _ok("completed")
        names = [e for e, _ in t.events]
        return _fail("completed", f"no `done` event; stream ended on {names[-3:]}")
    return check


def committed_at_least(n: int = 1) -> Check:
    def check(t: TurnResult) -> CheckResult:
        spoken = [m for m in t.messages if m.character_id is not None]
        if len(spoken) >= n:
            return _ok("committed_at_least", f"{len(spoken)} message(s)")
        return _fail(
            "committed_at_least",
            f"expected >= {n} generated message(s), got {len(spoken)}"
            + (" — LLM chose to do nothing" if t.events_named("notice") else ""),
        )
    return check


def no_blank_messages() -> Check:
    """Nothing empty or whitespace-only got persisted.

    Beat 3 (`beat-the-battle`) ships an empty `starter_prompt`, so entering the
    boss fight writes a blank DM message to the save. This is the check that
    surfaces it.
    """
    def check(t: TurnResult) -> CheckResult:
        blanks = [m for m in t.messages if not m.content.strip()]
        if not blanks:
            return _ok("no_blank_messages")
        who = ", ".join(m.character_name or m.role for m in blanks)
        return _fail(
            "no_blank_messages",
            f"{len(blanks)} blank message(s) committed ({who})",
        )
    return check


def no_fallback() -> Check:
    """No phase exhausted its retries and fell back to canned output."""
    def check(t: TurnResult) -> CheckResult:
        fallbacks = t.markers_named("validation.fallback")
        if not fallbacks:
            return _ok("no_fallback")
        detail = "; ".join(
            f"{m.get('call')}: {m.get('reason')}" for m in fallbacks
        )
        return _fail("no_fallback", detail)
    return check


def stream_matches_save() -> Check:
    """What the player saw streamed matches what was persisted."""
    def check(t: TurnResult) -> CheckResult:
        persisted = {
            m.character_id: m.content
            for m in t.messages
            if m.character_id is not None
        }
        if not persisted or not t.streamed:
            return _skip("stream_matches_save", "nothing streamed this turn")
        mismatched = [
            cid for cid, text in t.streamed.items()
            if cid in persisted and text.strip() != persisted[cid].strip()
        ]
        if not mismatched:
            return _ok("stream_matches_save")
        return _fail(
            "stream_matches_save",
            f"streamed text differs from saved text for: {', '.join(mismatched)}",
        )
    return check


# ── Beat progression ──────────────────────────────────────────────────────────

def beat_is(beat_id: str) -> Check:
    def check(t: TurnResult) -> CheckResult:
        if t.beat_after == beat_id:
            return _ok("beat_is", beat_id)
        return _fail("beat_is", f"expected beat {beat_id}, on {t.beat_after or 'none'}")
    return check


def beat_unchanged() -> Check:
    """The story must not move. Used for off-script actions that satisfy no
    transition condition — an advance here means the Director rubber-stamped."""
    def check(t: TurnResult) -> CheckResult:
        if t.beat_before == t.beat_after:
            return _ok("beat_unchanged", t.beat_after or "none")
        return _fail(
            "beat_unchanged",
            f"beat advanced {t.beat_before or 'none'} → {t.beat_after} "
            f"on an action that satisfies no transition condition",
        )
    return check


def beat_advanced_to(beat_id: str) -> Check:
    def check(t: TurnResult) -> CheckResult:
        if t.beat_after == beat_id and t.beat_before != beat_id:
            return _ok("beat_advanced_to", beat_id)
        if t.beat_after == beat_id:
            return _warn("beat_advanced_to", f"already on {beat_id} before this step")
        return _fail(
            "beat_advanced_to",
            f"expected advance to {beat_id}, ended on {t.beat_after or 'none'}",
        )
    return check


def first_transition_is_lowest_order(lowest_beat_id: str) -> Check:
    """The first transition out of a beatless save must land on the opening beat.

    A save starts with `current_beat_id=null`, and `validate_director_response`
    only enforces forward ordering when there *is* a current beat — so from null
    the Director may legally name any beat, including the last one. Self-skips
    unless this turn is that first transition, so it is safe on every step.
    """
    def check(t: TurnResult) -> CheckResult:
        if t.beat_before is not None:
            return _skip("first_transition_is_lowest_order", "not the first transition")
        if not t.beat_advanced:
            return _skip("first_transition_is_lowest_order", "no transition this turn")
        if t.beat_after == lowest_beat_id:
            return _ok("first_transition_is_lowest_order", lowest_beat_id)
        return _fail(
            "first_transition_is_lowest_order",
            f"first transition skipped ahead to {t.beat_after} instead of "
            f"{lowest_beat_id} — the forward-order guard does not apply from a "
            f"null current_beat_id",
        )
    return check


def advance_matches_dice_outcome() -> Check:
    """Dice are unseeded, so the outcome is whatever it is — but the *rule* is
    fixed: `beat_advance` is only eligible on success. Assert the rule against
    the roll that actually happened."""
    def check(t: TurnResult) -> CheckResult:
        roll = t.roll
        if roll is None:
            return _skip("advance_matches_dice_outcome", "no dice this turn")
        succeeded = outcome_is_success(roll.get("outcome", ""))
        advanced = t.beat_advanced
        summary = f"{roll.get('dice')} {roll.get('value')}/{roll.get('max_value')} → {roll.get('outcome')}"
        if succeeded and not advanced:
            return _warn(
                "advance_matches_dice_outcome",
                f"{summary} but no beat advance — Director declined the transition",
            )
        if not succeeded and advanced:
            return _fail(
                "advance_matches_dice_outcome",
                f"{summary} yet the beat advanced anyway",
            )
        if not succeeded:
            blocked = [
                m for m in t.markers_named("beat.blocked")
                if m.get("reason") == "dice_failure"
            ]
            if t.request.get("beat_advance") and not blocked:
                return _warn(
                    "advance_matches_dice_outcome",
                    f"{summary}; advance correctly withheld but no beat.blocked marker",
                )
        return _ok("advance_matches_dice_outcome", summary)
    return check


def ending_reached() -> Check:
    def check(t: TurnResult) -> CheckResult:
        events = t.events_named("ending_reached")
        if not events:
            return _fail("ending_reached", f"no ending_reached event (beat={t.beat_after})")
        if len(events) > 1:
            return _fail("ending_reached", f"{len(events)} ending_reached events, want 1")
        payload = events[0] if isinstance(events[0], dict) else {}
        if payload.get("sandbox_mode") is not True:
            return _fail("ending_reached", f"sandbox_mode not set in payload: {payload}")
        if not t.sandbox_after:
            return _fail("ending_reached", "save was not left in sandbox mode")
        return _ok("ending_reached")
    return check


# ── Options ───────────────────────────────────────────────────────────────────

def options_between(lo: int = 2, hi: int = 6) -> Check:
    def check(t: TurnResult) -> CheckResult:
        n = len(t.options)
        if lo <= n <= hi:
            return _ok("options_between", f"{n} options")
        return _fail("options_between", f"expected {lo}-{hi} options, got {n}")
    return check


def at_most_one_advancing_option() -> Check:
    def check(t: TurnResult) -> CheckResult:
        flagged = [o for o in t.options if o.get("advances_beat")]
        if len(flagged) <= 1:
            return _ok("at_most_one_advancing_option", f"{len(flagged)} flagged")
        return _fail(
            "at_most_one_advancing_option",
            f"{len(flagged)} options flagged advances_beat (clamping failed)",
        )
    return check


def advance_flag_is_actionable() -> Check:
    """An option flagged `advances_beat` that the flag handler cannot act on is a
    dead button: clicking it sets beat_advance on a turn where nothing happens.

    `transition_offered` now tracks `phases.classify_advance`, which mirrors the
    player-flag branch in routes/chat.py. Note that the final beat is NOT a dead
    button — there the flag signals the ending — so only sandbox mode and beatless
    scenarios trip this. The marker also carries `advance_kind` for the detail."""
    def check(t: TurnResult) -> CheckResult:
        generated = t.markers_named("options.generated")
        if not generated:
            return _skip("advance_flag_is_actionable", "no options marker this turn")
        m = generated[-1]
        flagged = m.get("advance_idx") not in (None, "-")
        possible = m.flag("transition_offered")
        if flagged and not possible:
            return _warn(
                "advance_flag_is_actionable",
                f"an option is flagged advances_beat but the flag is inert here "
                f"(advance_kind={m.get('advance_kind') or '-'}) — the toggle would be a no-op",
            )
        return _ok("advance_flag_is_actionable")
    return check


def speaker_included(character_id: str) -> Check:
    """Soft: a specific companion took a turn. Their persona is the thing being
    exercised, but the Director legitimately picks one speaker per turn."""
    def check(t: TurnResult) -> CheckResult:
        if t.spoke(character_id):
            return _ok("speaker_included", character_id)
        spoke = ", ".join(sorted({m.character_id or "?" for m in t.messages})) or "nobody"
        return _warn("speaker_included", f"{character_id} silent; spoke: {spoke}")
    return check
