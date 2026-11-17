"""Explosive targets declare individual defenses before any attack dice exist."""

import random

from lorekit.alien import area, characters, combat, scenes
from lorekit.alien.state import initial


def test_area_defenses_precede_roll_and_primary_miss_suppresses_blast():
    b = {
        "collections": {
            "items": {"launcher": {"damage": 1, "maximum": 2, "capacity": 2, "supply_type": "ammo", "explosive": True}}
        }
    }
    s = initial()
    for aid in ("shooter", "primary", "secondary"):
        s["actors"][aid] = characters.build(
            {
                "name": aid,
                "kind": "npc",
                "attributes": dict(strength=5, agility=5, wits=3, empathy=3),
                "inventory": [{"id": "launcher", "content_id": "launcher", "equipped": True}]
                if aid == "shooter"
                else [],
            },
            b,
            imported=True,
        )
    scenes.setup(
        s,
        {
            "zones": {"a": {}, "b": {}},
            "borders": [{"a": "a", "b": "b", "open": True}],
            "placements": {"shooter": "a", "primary": "b", "secondary": "b"},
        },
    )
    scenes.start_combat(s, {"actors": list(s["actors"]), "surprise": ["shooter"]}, random.Random(1))
    out = combat.declare(s, {"actor": "shooter", "target": "primary", "weapon": "launcher"}, b)
    out = combat.defend(
        s, {"actor": "primary", "choice": "decline", "decision_id": out["decision_id"]}, b, random.Random(1)
    )
    assert out["pending"]["type"] == "area_defenses"
    assert "roll" not in str(out)
    result = area.decide(s, {"actor": "secondary", "choice": "dodge"}, b, random.Random(1))
    assert result["type"] == "check_complete"
    assert len(result["checks"]) == 2
    assert s["combat"]["budgets"]["secondary"]["quick"] == 1
    result["successes"] = [0, 0]
    area.finish(s, result, b, random.Random(1))
    assert s["actors"]["primary"]["health"] == s["actors"]["secondary"]["health"] == 5


def test_quick_shot_can_resolve_two_blast_zones_with_separate_dodges():
    b = {
        "collections": {
            "talents": {"quick": {"effects": [{"hook": "quick_shot"}]}},
            "items": {"launcher": {"damage": 1, "maximum": 2, "capacity": 3, "supply_type": "ammo", "explosive": True}},
        }
    }
    s = initial()
    for aid in ("a", "b", "c", "d"):
        s["actors"][aid] = characters.build(
            {
                "name": aid,
                "kind": "npc",
                "attributes": dict(strength=5, agility=5, wits=3, empathy=3),
                "skills": {"ranged_combat": 5},
                "talents": ["quick"] if aid == "b" else [],
                "inventory": [{"id": "gun", "content_id": "launcher", "equipped": True}] if aid in ("a", "b") else [],
            },
            b,
            imported=True,
        )
    scenes.setup(
        s,
        {
            "zones": {"left": {}, "right": {}},
            "borders": [{"a": "left", "b": "right", "open": True}],
            "placements": {"a": "left", "d": "left", "b": "right", "c": "right"},
        },
    )
    scenes.start_combat(s, {"actors": list(s["actors"]), "surprise": ["a"]}, random.Random(1))
    out = combat.declare(s, {"actor": "a", "target": "b", "weapon": "gun"}, b)
    out = combat.defend(
        s,
        {"actor": "b", "choice": "quick_shot", "weapon": "gun", "decision_id": out["decision_id"]},
        b,
        random.Random(5),
    )
    assert [entry["actor"] for entry in s["pending"]["targets"]] == ["c", "d"]
    area.decide(s, {"actor": "c", "choice": "decline"}, b, random.Random(5))
    result = area.decide(s, {"actor": "d", "choice": "decline"}, b, random.Random(5))
    assert result["successes"] == [4, 1]
    area.finish(s, result, b, random.Random(5))
    assert s["actors"]["a"]["health"] == s["actors"]["d"]["health"] == 4
    assert s["actors"]["b"]["health"] == s["actors"]["c"]["health"] == 1
    assert s["actors"]["a"]["inventory"][0]["supply"] == 2
    assert s["actors"]["b"]["inventory"][0]["supply"] == 2
