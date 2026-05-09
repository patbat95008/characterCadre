"""Unit tests for SillyTavern v2 import / export."""
from __future__ import annotations

import pytest

from app import silly_tavern


SAMPLE_CARD = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "Astra",
        "description": "A wandering star-mage.",
        "personality": "Curious, soft-spoken, ancient.",
        "scenario": "Travelling through the desert.",
        "first_mes": "Hello traveller.",
        "mes_example": (
            "<START>\n"
            "{{user}}: Who are you?\n"
            "{{char}}: I am Astra, keeper of the night sky.\n"
            "<START>\n"
            "{{user}}: Where are you going?\n"
            "{{char}}: Wherever the stars lead me."
        ),
    },
}


def test_import_basic_fields():
    char = silly_tavern.import_silly_tavern_v2(SAMPLE_CARD)
    assert char.name == "Astra"
    assert "wandering star-mage" in char.description
    assert "Curious, soft-spoken" in char.description
    assert char.is_dm is False


def test_import_parses_mes_example_into_pairs():
    char = silly_tavern.import_silly_tavern_v2(SAMPLE_CARD)
    assert len(char.response_examples) == 2
    assert char.response_examples[0]["user"] == "Who are you?"
    assert "Astra" in char.response_examples[0]["char"]


def test_import_handles_missing_personality():
    payload = {"data": {"name": "Solo", "description": "Just a description.", "mes_example": ""}}
    char = silly_tavern.import_silly_tavern_v2(payload)
    assert char.description == "Just a description."
    assert char.response_examples == []


def test_import_handles_missing_description():
    payload = {"data": {"name": "P", "personality": "Just personality.", "mes_example": ""}}
    char = silly_tavern.import_silly_tavern_v2(payload)
    assert char.description == "Just personality."


def test_import_rejects_missing_data_object():
    with pytest.raises(ValueError):
        silly_tavern.import_silly_tavern_v2({"spec": "v2"})


def test_import_rejects_missing_name():
    with pytest.raises(ValueError):
        silly_tavern.import_silly_tavern_v2({"data": {"description": "no name"}})


def test_import_skips_malformed_blocks():
    """A block missing a {{user}} or {{char}} marker should not produce a pair."""
    raw = {
        "data": {
            "name": "Glitchy",
            "description": "test",
            "mes_example": "<START>\n{{char}}: hello with no user before me\n<START>\n{{user}}: a\n{{char}}: b",
        }
    }
    char = silly_tavern.import_silly_tavern_v2(raw)
    assert len(char.response_examples) == 1
    assert char.response_examples[0] == {"user": "a", "char": "b"}


def test_export_omits_internal_fields():
    char = silly_tavern.import_silly_tavern_v2(SAMPLE_CARD)
    char.description_summary = "internal summary"
    char.description_hash = "abcdef"
    exported = silly_tavern.export_silly_tavern_v2(char)
    assert "description_summary" not in exported["data"]
    assert "description_hash" not in exported["data"]
    assert exported["spec"] == "chara_card_v2"


def test_round_trip_preserves_pairs():
    char = silly_tavern.import_silly_tavern_v2(SAMPLE_CARD)
    exported = silly_tavern.export_silly_tavern_v2(char)
    re_imported = silly_tavern.import_silly_tavern_v2(exported)
    assert re_imported.name == char.name
    assert re_imported.response_examples == char.response_examples
