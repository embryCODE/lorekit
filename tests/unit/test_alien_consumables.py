"""Original dose fixtures verify expiry, overdoses and resource consumption."""

import random

import pytest

from lorekit.alien import actions, characters, checks, consumables, survival
from lorekit.alien.state import initial
from lorekit.db import LoreKitError


def setup():
    s = initial()
    b = {
        "collections": {
            "items": {
                kind: {"weight": 0, "consume": kind}
                for kind in ["wake_day", "stress_zero", "hypersleep_relief", "coffee"]
            }
        }
    }
    s["actors"]["a"] = characters.build(
        {
            "name": "A",
            "attributes": dict(strength=3, agility=3, wits=3, empathy=3),
            "stress": 4,
            "inventory": [{"id": kind, "content_id": kind, "quantity": 3} for kind in b["collections"]["items"]],
        },
        b,
        imported=True,
    )
    return s, b


def test_overdose_penalty_expires_and_consumed_dose_cannot_repeat():
    s, b = setup()
    for _ in range(3):
        actions.act(s, {"actor": "a", "action": "consume", "item": "stress_zero"}, b, random.Random(1))
    a = s["actors"]["a"]
    assert a["stress"] == 0 and a["dose_penalty"] == 2
    check = checks.roll(s, {"actor": "a", "skill": "comtech"}, b, random.Random(1))
    assert len(check["roll"]["base"]) == 1
    with pytest.raises(LoreKitError, match="consumed"):
        actions.act(s, {"actor": "a", "action": "consume", "item": "stress_zero"}, b, random.Random(1))
    survival.advance_time(s, "shift", 1)
    assert len(checks.roll(s, {"actor": "a", "skill": "comtech"}, b, random.Random(1))["roll"]["base"]) == 3


def test_wake_effect_blocks_voluntary_relief_for_a_day_and_coffee_does_not_stack():
    s, b = setup()
    a = s["actors"]["a"]
    actions.act(s, {"actor": "a", "action": "consume", "item": "wake_day"}, b, random.Random(1))
    assert a["stress"] == 5
    out = actions.act(s, {"actor": "a", "action": "consume", "item": "stress_zero"}, b, random.Random(1))
    assert not out["stress_relief"] and a["stress"] == 5
    actions.act(s, {"actor": "a", "action": "consume", "item": "coffee"}, b, random.Random(1))
    actions.act(s, {"actor": "a", "action": "consume", "item": "coffee"}, b, random.Random(1))
    assert a["stress"] == 6
    survival.advance_time(s, "day", 1)
    assert consumables.relieve(s, "a", 1)


def test_hypersleep_relief_preserves_other_fatigue_sources():
    s, b = setup()
    a = s["actors"]["a"]
    a.update(
        fatigued=True,
        hazards=[{"id": kind, "kind": kind, "unit": "shift", "due": 1} for kind in ["hypersleep", "hunger"]],
    )
    actions.act(s, {"actor": "a", "action": "consume", "item": "hypersleep_relief"}, b, random.Random(1))
    assert a["fatigued"] and [h["id"] for h in a["hazards"]] == ["hunger"]
