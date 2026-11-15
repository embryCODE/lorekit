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
