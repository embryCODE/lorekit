"""Environmental rule boundaries with original actor fixtures."""

import random

from lorekit.alien import environment, scenes, survival
from lorekit.alien.characters import build
from lorekit.alien.state import initial


class Sixes:
    def randint(self, low, high):
        return 6


def setup():
    s = initial()
    b = {"collections": {}}
    s["actors"]["crew"] = build(
        {"name": "Crew", "attributes": dict(strength=3, agility=3, wits=3, empathy=3)}, b, imported=True
    )
    scenes.setup(
        s,
        {
            "zones": {"safe": {}, "fire": {}},
            "borders": [{"a": "safe", "b": "fire", "open": True}],
            "placements": {"crew": "safe"},
        },
    )
    return s, b


def test_entering_fire_applies_damage_and_breaking_uses_environmental_death():
    s, b = setup()
    environment.act(s, {"action": "fire_set", "zone": "fire", "intensity": 4}, b, Sixes())
    result = scenes.move(s, "crew", "fire", b, rng=Sixes())
    a = s["actors"]["crew"]
    assert result["fire"]["dice"] == [6] * 4
    assert a["health"] == 0
    assert not a["injuries"]
    assert a["hazards"][0]["kind"] == "environmental_death"


def test_radiation_accumulates_without_armor_and_recovers_after_leaving():
    s, b = setup()
    a = s["actors"]["crew"]
    a["armor"] = 10
    result = environment.radiation(s, {"actor": "crew", "level": "strong"}, b, Sixes())
    assert a["health"] == 2
    assert a["rads"] == 1
    assert a["radiation_unit"] == "stretch"
    environment.act(s, {"action": "leave_radiation", "actor": "crew"}, b, Sixes())
    survival.advance_time(s, "shift", 1)
    assert a["rads"] == 0


def test_decompression_vents_in_order_and_checks_precede_actions():
    from lorekit.alien.engine import complete_check

    s, b = setup()
    environment.breach(s, ["safe", "fire"])
    assert not s["scene"]["vents"]["safe"]["vacuum"]
    survival.advance_time(s, "stretch", 1)
    assert s["scene"]["vents"]["safe"]["vacuum"]
    assert not s["scene"]["vents"]["fire"]["vacuum"]
    # Install no-check content provenance for the preview transition.
    result = environment.pressure_guard(s, {"op": "check", "actor": "crew", "skill": "comtech"}, b, random.Random(1))
    assert s["pending"]["continuation"]["kind"] == "pressure"
    assert s["actors"]["crew"]["stress"] == 1
    assert s["actors"]["crew"]["vacuum_checks"] == 1
