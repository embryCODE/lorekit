"""Damage and supply rules tested with original numerical fixtures."""

import copy
import random

import pytest

from lorekit.alien import content, survival
from lorekit.alien.characters import build
from lorekit.alien.state import initial


def setup():
    bundle = {
        "profile": "evolved-beta2",
        "source": "Original test fixtures",
        "collections": {
            "injuries": {
                str(t * 10 + u): {"page": 1, "effects": [], "lethal": False} for t in range(1, 7) for u in range(1, 7)
            },
            "items": {"meter": {"page": 1, "capacity": 8, "supply_type": "power", "effects": []}},
        },
    }
    a = build(
        {
            "name": "Example",
            "attributes": dict(strength=3, agility=3, wits=3, empathy=3),
            "inventory": [{"id": "meter", "content_id": "meter"}],
        },
        bundle,
        imported=True,
    )
    s = initial()
    s["actors"]["example"] = a
    return s, bundle


def test_supply_caps_at_six_dice_and_only_ones_deplete():
    s, b = setup()
    result = survival.supply(s["actors"]["example"], "meter", b, random.Random(2))
    assert len(result["dice"]) == 6
    assert result["remaining"] == 8 - result["dice"].count(1)


def test_barrier_base_damage_limit_ignores_extra_successes():
    s, b = setup()
    events = survival.damage(s, "example", 12, b, random.Random(2), base_damage=1, barrier_armor=2)
    assert events[0]["inflicted"] == 0
    assert s["actors"]["example"]["health"] == 3


def test_armor_reduction_and_fatal_threshold():
    s, b = setup()
    a = s["actors"]["example"]
    a["armor"] = 2
    survival.damage(s, "example", 3, b, random.Random(2))
    assert a["health"] == 2
    assert not a["injuries"]
    survival.damage(s, "example", 9, b, random.Random(2))
    assert a["dead"]


def test_synthetic_damage_escalates_and_does_not_use_human_injuries():
    s, b = setup()
    a = s["actors"]["example"]
    a["species"] = "synthetic"
    survival.damage(s, "example", 4, b, random.Random(1))
    first = a["system_damage"][0]
    survival.damage(s, "example", 1, b, random.Random(1))
    assert a["system_damage"] == [first, first + 1]
    assert not a["injuries"]


def test_rally_does_not_stabilize_but_first_aid_does():
    s, b = setup()
    a = s["actors"]["example"]
    a["health"] = 0
    a["injuries"] = [{"id": "61", "effects": [], "lethal": True, "stabilized": False, "interval": "round", "due": 1}]
    result = {"success": True, "successes": [1], "checks": [], "continuation": {"method": "rally", "target": "example"}}
    survival.finish_recover(s, result, b, random.Random(1))
    assert a["health"] == 1
    assert not a["injuries"][0]["stabilized"]
    result["continuation"]["method"] = "first_aid"
    survival.finish_recover(s, result, b, random.Random(1))
    assert a["injuries"][0]["stabilized"]


def test_failed_first_aid_shortens_deadline_and_can_kill():
    s, b = setup()
    a = s["actors"]["example"]
    a["injuries"] = [{"id": "61", "effects": [], "lethal": True, "stabilized": False, "interval": "stretch", "due": 1}]
    result = {
        "success": False,
        "successes": [0],
        "checks": [],
        "continuation": {"method": "first_aid", "target": "example"},
    }
    survival.finish_recover(s, result, b, random.Random(1))
    assert a["injuries"][0]["interval"] == "round"
    survival.finish_recover(s, result, b, random.Random(1))
    assert a["dead"]


def test_interrupted_rest_does_not_advance_clock_or_recover():
    s, b = setup()
    a = s["actors"]["example"]
    a.update(health=1, stress=3)
    survival.rest(s, {"actor": "example", "safe": True, "interrupted": True}, b, random.Random(1))
    assert s["clocks"]["stretch"] == 0
    assert a["health"] == 1
    assert a["stress"] == 3


def test_critical_injury_healing_waits_for_surgery_and_health_is_independent():
    s, b = setup()
    a = s["actors"]["example"]
    a["injuries"] = [
        {
            "id": "test",
            "effects": [],
            "lethal": False,
            "stabilized": True,
            "interval": None,
            "due": None,
            "surgery_required": True,
            "healing_remaining": 2,
            "healing_unit": "day",
            "prosthesis_required": False,
        }
    ]
    survival.advance_time(s, "day", 1)
    assert a["injuries"][0]["healing_remaining"] == 2
    a["injuries"][0]["surgery_required"] = False
    survival.advance_time(s, "day", 1)
    assert a["injuries"][0]["healing_remaining"] == 1
    assert a["health"] == a["health_max"]
    survival.advance_time(s, "day", 1)
    assert a["injuries"] == []


def test_sleep_loss_forces_one_shift_and_hypersleep_needs_a_meal():
    from lorekit.alien.effects import drain

    class Ones:
        def randint(self, low, high):
            return 1

    s, b = setup()
    a = s["actors"]["example"]
    a.update(health=1, fatigued=True, stress=1)
    survival.hazard(s, {"actor": "example", "kind": "sleep"}, b, Ones())
    survival.advance_time(s, "shift", 1)
    out = drain(s, Ones(), bundle=b)
    # The failed active Survival roll has a stress symbol and completes without a push.
    assert a["unconscious"] and a["hazards"][0]["kind"] == "forced_sleep"
    survival.advance_time(s, "shift", 1)
    drain(s, Ones(), bundle=b)
    assert not a["unconscious"] and not a["fatigued"]
    survival.hazard(s, {"actor": "example", "kind": "hypersleep"}, b, Ones())
    from lorekit.db import LoreKitError

    with pytest.raises(LoreKitError, match="awake shift"):
        survival.remedy(s, {"actor": "example", "hazard": "hypersleep", "reason": "Meal", "meal": True})
    survival.advance_time(s, "shift", 1)
    drain(s, Ones(), bundle=b)
    assert a["fatigued"]
    survival.remedy(
        s, {"actor": "example", "hazard": "hypersleep", "reason": "Awake for a shift with a meal", "meal": True}
    )
    assert not a["fatigued"]
