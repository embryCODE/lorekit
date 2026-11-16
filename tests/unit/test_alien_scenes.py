"""Scene and action accounting boundaries independent of proprietary catalogs."""

import random

import pytest

from lorekit.alien import scenes
from lorekit.alien.characters import build
from lorekit.alien.state import initial
from lorekit.db import LoreKitError


def state():
    s = initial()
    s["actors"] = {
        str(i): build(
            {"name": str(i), "attributes": dict(strength=3, agility=3, wits=4, empathy=4)},
            {"collections": {}},
            imported=True,
        )
        for i in range(3)
    }
    scenes.setup(
        s,
        {
            "zones": {"room": {"cluttered": True}, "hall": {}},
            "borders": [{"a": "room", "b": "hall", "door": True}],
            "placements": {aid: "room" for aid in s["actors"]},
        },
    )
    return s


def test_hold_off_does_not_refund_an_interrupt():
    s = state()
    scenes.start_combat(s, {"actors": list(s["actors"])}, random.Random(1))
    slots = s["combat"]["slots"]
    first, last = slots[0]["actors"][0], slots[-1]["actors"][0]
    scenes.spend(s, first, "quick", out_of_turn=True)
    scenes.hold_off(s, first, slots[-1]["card"])
    assert s["combat"]["budgets"][first]["quick"] == 1
    assert slots[0]["actors"] == [last]
    with pytest.raises(LoreKitError, match="turn"):
        scenes.spend(s, first, "full")


def test_interrupts_exhaust_full_action():
    s = state()
    scenes.start_combat(s, {"actors": list(s["actors"])}, random.Random(1))
    aid = s["combat"]["slots"][0]["actors"][0]
    scenes.spend(s, aid, "quick", out_of_turn=True)
    scenes.spend(s, aid, "quick", out_of_turn=True)
    with pytest.raises(LoreKitError, match="unavailable"):
        scenes.spend(s, aid, "full")


def test_sealed_border_rejects_movement_and_range_differentiates_adjacency():
    s = state()
    assert scenes.range_band(s, "0", "1") == 1
    s["actors"]["0"]["adjacent"] = ["1"]
    assert scenes.range_band(s, "0", "1") == 0
    s["scene"]["borders"][0]["sealed"] = True
    with pytest.raises(LoreKitError, match="traversable"):
        scenes.move(s, "0", "hall", {"collections": {}})


def test_initiative_overflow_does_not_create_duplicate_cards():
    import copy

    s = state()
    for i in range(3, 11):
        s["actors"][str(i)] = copy.deepcopy(s["actors"]["0"])
    with pytest.raises(LoreKitError, match="ten initiative"):
        scenes.start_combat(s, {"actors": list(s["actors"])}, random.Random(1))


def test_fractional_load_counts_reloads_excludes_drops_and_stresses_each_move():
    from lorekit.alien.characters import load_weight

    s = state()
    b = {"collections": {"items": {"sample": {"weight": 0.5, "capacity": 1}}}}
    a = s["actors"]["0"]
    a["inventory"] = [{"id": "load", "content_id": "sample", "quantity": 12, "reloads": 1}]
    assert load_weight(a, b) == (6.25, 6)
    scenes.move(s, "0", "hall", b)
    scenes.move(s, "0", "room", b)
    assert a["stress"] == 2
    a["inventory"][0]["dropped"] = True
    assert load_weight(a, b) == (0, 6)
    a["inventory"][0].update(dropped=False, quantity=25)
    with pytest.raises(LoreKitError, match="encumbrance"):
        scenes.move(s, "0", "hall", b)
    a["inventory"][0]["quantity"] = 12
    a["species"] = "synthetic"
    scenes.move(s, "0", "hall", b)
    assert a["stress"] == 2
