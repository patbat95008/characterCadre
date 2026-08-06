"""
Per-turn correlation context.

A turn fans out into ~7 Ollama calls across four phases. Nothing in the logs
tied those calls back to the turn that produced them, so `logs/ollama-*.log`
entries interleaved with no way to reconstruct a single turn.

These ContextVars carry a turn id and the current phase down through
phases.py → ollama_client.py → llm_logger.py without threading an extra
parameter through eight signatures.

CAVEAT: async generators do not get an isolated context copy in CPython, so a
`set()` inside one mutates the caller's context. The chat-turn route sets and
resets with a token to contain this. It is still only reliable when turns run
sequentially — which is the case for a single-player app and for the playtest
harness. Concurrent turns can observe each other's turn id.
"""
from __future__ import annotations

from contextvars import ContextVar

current_turn_id: ContextVar[str] = ContextVar("current_turn_id", default="")
current_phase: ContextVar[str] = ContextVar("current_phase", default="")

# Per-turn counters. Holds a *mutable* dict rather than an immutable value so
# nested coroutines can accumulate into it without a .set() that context
# copying might discard.
current_turn_stats: ContextVar[dict[str, int] | None] = ContextVar(
    "current_turn_stats", default=None
)


def get_turn_id() -> str:
    return current_turn_id.get()


def get_phase() -> str:
    return current_phase.get()


def bump(key: str, amount: int = 1) -> None:
    """Add to a per-turn counter. No-op outside a turn."""
    stats = current_turn_stats.get()
    if stats is not None:
        stats[key] = stats.get(key, 0) + amount


def get_stats() -> dict[str, int]:
    return dict(current_turn_stats.get() or {})
