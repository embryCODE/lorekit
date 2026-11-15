"""Creature-only dice and Speed use original, non-book signature fixtures."""

from lorekit.alien.content import install
from lorekit.alien.state import execute, load, verify
from lorekit.db import require_db


def test_signature_uses_explicit_dice_and_only_one_action_per_speed_slot(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    revision = 0

    def act(intent, seed=1):
        nonlocal revision
        result = execute(db, sid, intent, f"req-{revision}", revision, seed)
        revision = result["revision"]
        return result

    bundle = {
        "profile": "evolved-beta2",
        "source": "Original creature fixture",
        "collections": {
            "creatures": {
                "test-creature": {
                    "page": 1,
                    "health": 4,
                    "speed": 2,
                    "skills": {"mobility": 2, "observation": 1},
                    "attacks": {
                        str(i): {"id": f"test-{i}", "actions": [{"hook": "attack", "dice": 6, "damage": 1}]}
                        for i in range(1, 7)
                    },
                }
            },
            "injuries": {
                str(t * 10 + u): {"page": 1, "effects": [], "lethal": False} for t in range(1, 7) for u in range(1, 7)
            },
        },
    }
    act({"op": "content_import", "hash": install(bundle)})
    act({"op": "creature_import", "actor": "creature", "template": "test-creature", "name": "Test creature"})
    act(
        {
            "op": "character_import",
            "actor": "crew",
            "build": {"name": "Test crew", "kind": "npc", "attributes": dict(strength=5, agility=5, wits=3, empathy=3)},
        }
    )
    act({"op": "scene_setup", "zones": {"room": {}}, "placements": {"crew": "room", "creature": "room"}})
    start = act({"op": "combat_start", "actors": ["creature", "crew"], "surprise": ["creature"]})
    assert len(start["combat"]["slots"]) == 3
    attack = act({"op": "creature_act", "actor": "creature", "target": "crew", "feasible": [1, 2, 3, 4, 5, 6]})
    result = act({"op": "defense_decide", "actor": "crew", "choice": "decline", "decision_id": attack["decision_id"]})
    assert result["type"] == "signature_complete"
    assert len(result["events"][0]["checks"][0]["roll"]["base"]) == 6
    verify(load(db, sid))
    db.close()
