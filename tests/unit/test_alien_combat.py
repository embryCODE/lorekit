"""A personal attack through its real declaration/decision/persistence pipeline."""

import json

from lorekit.alien.content import install
from lorekit.alien.state import execute, load, verify
from lorekit.db import require_db


def test_attack_declares_before_roll_and_replays(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    revision = 0

    def act(intent, seed=3):
        nonlocal revision
        out = execute(db, sid, intent, f"request-{revision}", revision, seed)
        revision = out["revision"]
        return out

    bundle = {
        "profile": "evolved-beta2",
        "source": "Original fixtures",
        "collections": {
            "injuries": {
                str(t * 10 + u): {"page": 1, "effects": [], "lethal": False} for t in range(1, 7) for u in range(1, 7)
            }
        },
    }
    act({"op": "content_import", "hash": install(bundle)})
    for aid in ("a", "b"):
        act(
            {
                "op": "character_import",
                "actor": aid,
                "build": {
                    "name": aid,
                    "kind": "npc",
                    "attributes": dict(strength=5, agility=5, wits=3, empathy=3),
                    "skills": {"close_combat": 5},
                },
            }
        )
    act({"op": "scene_setup", "zones": {"room": {}}, "placements": {"a": "room", "b": "room"}})
    act({"op": "approach", "actor": "a", "target": "b"})
    start = act({"op": "combat_start", "actors": ["a", "b"]})
    attacker = start["combat"]["slots"][0]["actors"][0]
    target = "a" if attacker == "b" else "b"
    declared = act({"op": "attack", "actor": attacker, "target": target})
    assert declared["type"] == "defense_required"
    assert load(db, sid)["state"]["actors"][target]["health"] == 5
    out = act({"op": "defense_decide", "actor": target, "decision_id": declared["decision_id"], "choice": "decline"})
    assert out["type"] == "attack_complete"
    assert out["events"][0]["checks"][0]["pushes"] == 0
    verify(load(db, sid))
    db.close()


def test_known_full_cover_exception_does_not_reveal_unknown_target():
    import random

    import pytest

    from lorekit.alien import characters, combat, scenes
    from lorekit.alien.state import initial
    from lorekit.db import LoreKitError

    b = {"collections": {"items": {"weapon": {"damage": 1, "maximum": 2}}}}
    s = initial()
    for aid in ("a", "b"):
        s["actors"][aid] = characters.build(
            {
                "name": aid,
                "attributes": dict(strength=3, agility=3, wits=3, empathy=3),
                "inventory": [{"id": "gun", "content_id": "weapon", "equipped": True}],
            },
            b,
            imported=True,
        )
    scenes.setup(s, {"zones": {"room": {}}, "placements": {"a": "room", "b": "room"}})
    scenes.start_combat(s, {"actors": ["a", "b"], "surprise": ["a"]}, random.Random(1))
    s["actors"]["b"].update(cover="full", hidden=True)
    intent = {"actor": "a", "target": "b", "weapon": "gun"}
    with pytest.raises(LoreKitError, match="Target not visible"):
        combat.declare(s, intent, b)
    assert s["pending"] is None
    result = combat.declare(s, {**intent, "known_target": True}, b)
    assert result["type"] == "defense_required"
    assert s["pending"]["attack"]["modifier"] == -3
    assert s["actors"]["b"]["hidden"]
