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
