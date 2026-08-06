"""
Shared numeric constants.

Centralised so a single edit propagates everywhere instead of hunting through
phases.py, prompt_builder.py, validation.py, and models.py for stray literals.
Values intentionally match the originals — this module is a refactor, not a
behavior change.
"""
from __future__ import annotations

# Token budget reserved for the LLM's response when truncating chat history.
DEFAULT_RESPONSE_RESERVE = 1024

# Default per-save context window (overridable per save via Save.max_context_tokens).
DEFAULT_MAX_CONTEXT_TOKENS = 8192

# Minimum normalized length (in characters) before loop detection runs on a
# streamed response. Short replies collide too often to be checked safely.
LOOP_MIN_LENGTH = 40

# Inclusive bounds on the number of player options the LLM may return.
OPTIONS_MIN = 2
OPTIONS_MAX = 6
