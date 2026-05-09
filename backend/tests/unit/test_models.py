"""Round-trip serialization tests for all five domain models."""
import pytest
from app.models import Beat, Character, Message, Save, Scenario


class TestMessageModel:
    def test_minimal(self):
        msg = Message(id="m1", role="user", content="hello", timestamp="2026-01-01T00:00:00+00:00")
        assert msg.character_id is None
        assert msg.is_dm_only is False
        assert msg.beat_id_at_time is None

    def test_maximal(self):
        msg = Message(
            id="m2",
            role="dm",
            character_id="narrator",
            content="You see darkness.",
            timestamp="2026-01-01T00:00:00+00:00",
            is_dm_only=True,
            beat_id_at_time="beat-1",
        )
        assert msg.is_dm_only is True
        assert msg.beat_id_at_time == "beat-1"

    def test_roundtrip_dict(self):
        msg = Message(id="m3", role="character", character_id="bram", content="Aye.", timestamp="2026-01-01T00:00:00+00:00")
        assert Message.model_validate(msg.model_dump()) == msg

    def test_roundtrip_json(self):
        msg = Message(id="m4", role="dm", content="Scene.", timestamp="2026-01-01T00:00:00+00:00", is_dm_only=True)
        assert Message.model_validate_json(msg.model_dump_json()) == msg

    def test_invalid_role(self):
        with pytest.raises(Exception):
            Message(id="m5", role="invalid", content="x", timestamp="2026-01-01T00:00:00+00:00")  # type: ignore


class TestCharacterModel:
    def test_minimal(self):
        char = Character(id="c1", name="Alice", description="A hero.")
        assert char.is_dm is False
        assert char.description_summary == ""
        assert char.response_examples == []

    def test_maximal(self):
        char = Character(
            id="c2",
            name="The DM",
            description="Narrates everything.",
            description_summary="The narrator.",
            description_hash="abc123",
            response_examples=[{"user": "hi", "char": "hello"}],
            is_dm=True,
            avatar_path="/avatars/dm.png",
        )
        assert char.is_dm is True
        assert char.avatar_path == "/avatars/dm.png"

    def test_roundtrip_dict(self):
        char = Character(id="c3", name="Bob", description="A rogue.")
        assert Character.model_validate(char.model_dump()) == char

    def test_roundtrip_json(self):
        char = Character(
            id="c4", name="Mage", description="Casts spells.",
            response_examples=[{"user": "cast fireball", "char": "Boom."}],
            is_dm=False,
        )
        assert Character.model_validate_json(char.model_dump_json()) == char


class TestBeatModel:
    def test_minimal(self):
        beat = Beat(
            id="b1", order=0, name="Entrance", description="The dungeon gate.",
            transition_condition="The party enters.", starter_prompt="You enter."
        )
        assert beat.summary == ""
        assert beat.summary_hash == ""

    def test_maximal(self):
        beat = Beat(
            id="b2", order=1, name="Boss", description="A dragon waits.",
            summary="Epic confrontation.", summary_hash="deadbeef",
            transition_condition="Dragon defeated.", starter_prompt="A roar fills the chamber."
        )
        assert beat.summary == "Epic confrontation."

    def test_roundtrip_dict(self):
        beat = Beat(id="b3", order=2, name="Exit", description="Freedom.", transition_condition="Escape.", starter_prompt="You flee.")
        assert Beat.model_validate(beat.model_dump()) == beat

    def test_roundtrip_json(self):
        beat = Beat(
            id="b4", order=0, name="Start", description="Beginning.",
            summary="Intro scene.", summary_hash="ff00",
            transition_condition="Move forward.", starter_prompt="The adventure begins."
        )
        assert Beat.model_validate_json(beat.model_dump_json()) == beat


class TestScenarioModel:
    def _make_beat(self, order: int = 0) -> Beat:
        return Beat(
            id=f"beat-{order}", order=order, name=f"Beat {order}",
            description="A scene.", transition_condition="Move on.",
            starter_prompt="It begins."
        )

    def test_minimal(self):
        s = Scenario(id="s1", name="Cave", initial_message="Dark.", system_prompt="Be immersed.")
        assert s.beats == []
        assert s.dm_only_info == []

    def test_with_beats(self):
        beat = self._make_beat()
        s = Scenario(
            id="s2", name="Quest", initial_message="A journey.", system_prompt="Stay IC.",
            beats=[beat],
        )
        assert len(s.beats) == 1

    def test_maximal(self):
        s = Scenario(
            id="s3", name="Epic",
            summary="Grand tale.", summary_hash="sum123",
            initial_message="Once upon a time.", system_prompt="Collaborative RP.",
            persistent_messages=["The world is vast."],
            dm_only_info=["There is a trap."],
            recommended_character_ids=["char1"],
            beats=[self._make_beat(0), self._make_beat(1)],
        )
        assert len(s.beats) == 2
        assert s.dm_only_info == ["There is a trap."]

    def test_roundtrip_dict(self):
        s = Scenario(id="s4", name="Short", initial_message="Start.", system_prompt="Go.")
        assert Scenario.model_validate(s.model_dump()) == s

    def test_roundtrip_json(self):
        beat = self._make_beat()
        s = Scenario(
            id="s5", name="Full", initial_message="Hello.", system_prompt="Play.",
            beats=[beat], persistent_messages=["Note."], dm_only_info=["Secret."],
        )
        assert Scenario.model_validate_json(s.model_dump_json()) == s


class TestSaveModel:
    def _make_message(self, i: int) -> Message:
        return Message(id=f"msg-{i}", role="user", content=f"Message {i}.", timestamp="2026-01-01T00:00:00+00:00")

    def test_minimal(self):
        s = Save(
            id="save1", scenario_id="s1", name="Test", active_character_ids=["c1"],
            user_name="Player", created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
        )
        assert s.sandbox_mode is False
        assert s.current_beat_id is None
        assert s.messages == []
        assert s.max_context_tokens == 8192

    def test_maximal(self):
        s = Save(
            id="save2", scenario_id="s2", name="Big Save",
            active_character_ids=["narrator", "bram"],
            user_name="Hero",
            current_beat_id="beat-1",
            sandbox_mode=True,
            messages=[self._make_message(0), self._make_message(1)],
            max_context_tokens=4096,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
        )
        assert s.sandbox_mode is True
        assert s.current_beat_id == "beat-1"
        assert len(s.messages) == 2
        assert s.max_context_tokens == 4096

    def test_roundtrip_dict(self):
        s = Save(
            id="save3", scenario_id="s1", name="RT", active_character_ids=[],
            user_name="A", created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
        )
        assert Save.model_validate(s.model_dump()) == s

    def test_roundtrip_json(self):
        s = Save(
            id="save4", scenario_id="s1", name="JSON RT",
            active_character_ids=["c1", "c2"],
            user_name="Tester",
            messages=[self._make_message(0)],
            sandbox_mode=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        assert Save.model_validate_json(s.model_dump_json()) == s
