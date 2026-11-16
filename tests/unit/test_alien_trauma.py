"""Opt-in trauma mechanics with an original local fixture table."""

import random

import pytest

from lorekit.alien import checks, content, effects, trauma
from lorekit.alien.characters import build
from lorekit.alien.state import initial
from lorekit.db import LoreKitError


def setup():
    s = initial()
    b = {
        "profile": "evolved-beta2",
        "source": "Original test table",
        "collections": {
            "traumas": {
                str(t * 10 + u): {"page": 1, "effects": [{"hook": "trauma_trait", "trait": "no_push"}]}
                for t in range(1, 7)
                for u in range(1, 7)
            }
        },
    }
    content.validate(b)
    s["actors"]["crew"] = build(
        {"name": "Crew", "attributes": dict(strength=3, agility=3, wits=3, empathy=3)}, b, imported=True
    )
    return s, b


def test_disabled_trauma_and_once_per_completed_session():
    s, b = setup()
    with pytest.raises(LoreKitError, match="disabled"):
        trauma.start(s, {"actor": "crew", "chapter": "one"}, b, random.Random(1))
    s["options"]["mental_trauma"] = True
    assert trauma.start(s, {"actor": "crew", "chapter": "one"}, b, random.Random(1))["type"] == "trauma_not_triggered"
    with pytest.raises(LoreKitError, match="already"):
        trauma.start(s, {"actor": "crew", "chapter": "one"}, b, random.Random(1))


def test_trauma_blocks_push_and_extra_stress_applies_to_push_dice():
    s, b = setup()
    trauma.apply(s, "crew", {"id": "example", "effects": [{"hook": "trauma_trait", "trait": "no_push"}]})
    a = s["actors"]["crew"]
    check = checks.roll(s, {"actor": "crew", "skill": "comtech"}, b, random.Random(1))
    assert not checks.permission(a, check, b)
    a["trauma_traits"] = {"extra_stress": True}
    effects.stress(a, 1)
    assert a["stress"] == 2


def test_attribute_only_trauma_uses_no_stress_skill_penalties_or_story_prompt():
    class Twos:
        def randint(self, low, high):
            return 2

    s, b = setup()
    a = s["actors"]["crew"]
    s["options"]["mental_trauma"] = True
    a.update(highest_panic=9, stress=10, story_points=1, stress_responses=["aggravated"])
    result = trauma.start(s, {"actor": "crew", "chapter": "complete"}, b, Twos())
    assert result["type"] == "check_complete"
    assert result["checks"][0]["roll"] == {"base": [2, 2, 2], "stress": []}
    assert a["story_points"] == 1


def test_hallucination_cannot_be_skipped_or_charged_twice_for_a_shift():
    from lorekit.alien import survival

    s, b = setup()
    trauma.apply(s, "crew", {"id": "original", "effects": [{"hook": "trauma_trait", "trait": "hallucinations"}]})
    survival.advance_time(s, "shift", 1)
    effects.drain(s, random.Random(1), bundle=b)
    assert s["pending"]["type"] == "trauma_due"
    trauma.due_decide(s, {"actor": "crew", "reason": "GM describes an unsettling vision"})
    assert s["actors"]["crew"]["stress"] == 1
    trauma.event(s, {"actor": "crew", "event": "hallucination", "reason": "A vision during the new shift"})
    survival.advance_time(s, "shift", 1)
    effects.drain(s, random.Random(1), bundle=b)
    assert s["pending"] is None and s["actors"]["crew"]["stress"] == 2


def test_personality_keeps_body_and_skill_state_and_both_resolve_penalties():
    s, b = setup()
    b["collections"]["talents"] = {"example": {"page": 1, "effects": [{"hook": "resolve_bonus", "value": 1}]}}
    a = s["actors"]["crew"]
    trauma.apply(s, "crew", {"id": "offset", "effects": [{"hook": "resolve_delta", "value": -2}]})
    trauma.apply(s, "crew", {"id": "split", "effects": [{"hook": "trauma_trait", "trait": "personality_split"}]})
    trauma.decide(
        s,
        {
            "actor": "crew",
            "personality": {"name": "Other", "attributes": dict(strength=3, agility=3, wits=2, empathy=4)},
        },
        b,
        random.Random(1),
    )
    effects.stress(a, 1)
    assert a["name"] == "Other" and a["resolve"] == 2
    trauma.apply(s, "crew", {"id": "second-offset", "effects": [{"hook": "resolve_delta", "value": -2}]})
    effects.stress(a, 1)
    assert a["name"] == "Crew" and a["resolve"] == -1
    assert a["attributes"]["strength"] == 3 and a["health_max"] == 3
