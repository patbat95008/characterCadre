"""
SillyTavern v2 character card import / export.

This stage supports JSON cards only. PNG (tEXt-chunk) cards are deferred until
we add a PNG parsing dependency. The data model used by both formats is the
same — a v2 card has the shape:

    {
      "spec": "chara_card_v2",
      "spec_version": "2.0",
      "data": {
        "name": str,
        "description": str,
        "personality": str,
        "scenario": str,
        "first_mes": str,
        "mes_example": str,
        "creator_notes": str,
        ...
      }
    }

We map a v2 card into our Character model. On export we omit our internal
`description_summary` and `description_hash` fields — those are CharacterCadre-
specific and have no place in a portable card.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app.models import Character

logger = logging.getLogger(__name__)


# ── Import ────────────────────────────────────────────────────────────────────

def _parse_mes_example(raw: str) -> list[dict[str, str]]:
    """
    SillyTavern's mes_example field is a free-form string of conversation
    examples separated by `<START>` markers. Each block contains alternating
    lines like:
        {{user}}: hello
        {{char}}: hi there

    We split on <START> (case-insensitive, with surrounding whitespace) and
    then pair up consecutive {{user}}/{{char}} entries. Ragged inputs (a
    {{char}} without a preceding {{user}}, or vice versa) are dropped — we
    don't try to repair malformed cards.
    """
    if not raw or not raw.strip():
        return []

    blocks = re.split(r"\s*<\s*START\s*>\s*", raw, flags=re.IGNORECASE)
    pairs: list[dict[str, str]] = []

    line_re = re.compile(
        r"^[ \t]*\{\{(user|char)\}\}[ \t]*:[ \t]*",
        re.IGNORECASE | re.MULTILINE,
    )

    for block in blocks:
        if not block.strip():
            continue
        # Walk the block line-by-line so we can stitch multi-line speeches.
        # Find each marker, then take everything until the next marker.
        markers = list(line_re.finditer(block))
        if not markers:
            continue
        speeches: list[tuple[str, str]] = []
        for idx, m in enumerate(markers):
            speaker = m.group(1).lower()
            start = m.end()
            end = markers[idx + 1].start() if idx + 1 < len(markers) else len(block)
            text = block[start:end].strip()
            if text:
                speeches.append((speaker, text))

        # Pair them up: a {{user}} immediately followed by a {{char}} becomes one pair.
        i = 0
        while i < len(speeches) - 1:
            sp_a, text_a = speeches[i]
            sp_b, text_b = speeches[i + 1]
            if sp_a == "user" and sp_b == "char":
                pairs.append({"user": text_a, "char": text_b})
                i += 2
            else:
                i += 1

    return pairs


def import_silly_tavern_v2(payload: dict[str, Any], character_id: str | None = None) -> Character:
    """Convert a SillyTavern v2 JSON card dict into a Character.

    Raises ValueError if the payload doesn't look like a v2 card.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Expected SillyTavern v2 card with a 'data' object")

    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Card has no name")

    description = (data.get("description") or "").strip()
    personality = (data.get("personality") or "").strip()
    if description and personality:
        merged = description + "\n\n" + personality
    else:
        merged = description or personality

    response_examples = _parse_mes_example(data.get("mes_example") or "")

    return Character(
        id=character_id or str(uuid.uuid4()),
        name=name,
        description=merged,
        is_dm=False,
        response_examples=response_examples,
    )


# ── Export ────────────────────────────────────────────────────────────────────

def export_silly_tavern_v2(character: Character) -> dict[str, Any]:
    """Emit a SillyTavern v2 card dict from a Character.

    `description_summary` and `description_hash` are CharacterCadre-internal
    fields and are NOT included.
    """
    mes_example_blocks = []
    for pair in character.response_examples:
        u = pair.get("user", "").strip()
        c = pair.get("char", "").strip()
        if not u and not c:
            continue
        mes_example_blocks.append(
            f"<START>\n{{{{user}}}}: {u}\n{{{{char}}}}: {c}"
        )
    mes_example = "\n".join(mes_example_blocks)

    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": character.name,
            "description": character.description,
            "personality": "",
            "scenario": "",
            "first_mes": "",
            "mes_example": mes_example,
            "creator_notes": "Exported from CharacterCadre.",
            "system_prompt": "",
            "post_history_instructions": "",
            "tags": [],
            "creator": "",
            "character_version": "1.0",
            "alternate_greetings": [],
            "extensions": {},
        },
    }
