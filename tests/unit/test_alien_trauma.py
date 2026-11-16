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
