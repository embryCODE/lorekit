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


def test_infeasible_signature_selection_does_not_spin():
    import random

    import pytest

    from lorekit.alien import creatures
    from lorekit.alien.state import initial
    from lorekit.db import LoreKitError

    b = {
        "collections": {
            "creatures": {
                "test": {
                    "health": 4,
                    "speed": 2,
                    "skills": {},
                    "attacks": {
                        str(i): {"id": "one-shared-attack", "actions": [{"hook": "stress", "value": 1}]}
                        for i in range(1, 7)
                    },
                }
            }
        }
    }
    s = initial()
    s["actors"]["creature"] = creatures.build("test", "Example", b)
    s["actors"]["creature"]["last_attack"] = "one-shared-attack"
    with pytest.raises(LoreKitError, match="No feasible"):
        creatures.select(s, "creature", [1, 2, 3, 4, 5, 6], b, random.Random(1))


def test_egg_release_spawns_identity_and_immediate_attack_replays(make_session):
    from lorekit.alien import creatures
    from lorekit.alien.state import KEY, canonical

    sid = make_session(system="alien-evolved")
    db = require_db()
    bundle = {
        "profile": "evolved-beta2",
        "source": "Original egg fixture",
        "collections": {
            "creatures": {
                "egg": {"page": 1, "stage": "egg", "health": 2, "speed": 0, "skills": {}, "spawn": "hatchling"},
                "hatchling": {
                    "page": 1,
                    "health": 2,
                    "speed": 2,
                    "skills": {},
                    "attacks": {
                        str(i): {"id": str(i), "actions": [{"hook": "stress", "value": 1}]} for i in range(1, 7)
                    },
                },
            }
        },
    }
    revision = 0

    def act(intent):
        nonlocal revision
        result = execute(db, sid, intent, f"req-{revision}", revision, 1)
        revision = result["revision"]
        return result

    act({"op": "content_import", "hash": install(bundle)})
    act({"op": "creature_import", "actor": "egg", "template": "egg", "name": "Egg"})
    act(
        {
            "op": "character_import",
            "actor": "crew",
            "build": {"name": "Crew", "attributes": dict(strength=3, agility=3, wits=3, empathy=3)},
        }
    )
    act({"op": "scene_setup", "zones": {"room": {}}, "placements": {"egg": "room", "crew": "room"}})
    # Round-start opening uses the physical stress die, i.e. a one.
    # Search a deterministic seed that opens the egg after initiative draws.
    act({"op": "combat_start", "actors": ["egg", "crew"]})
    # The dedicated operation is added below to let the GM resolve an observed egg disturbance.
    out = act({"op": "egg_release", "actor": "egg", "target": "crew", "reason": "GM-observed egg disturbance"})
    state = load(db, sid)["state"]
    assert state["actors"]["egg"]["opened"]
    assert state["actors"]["egg-hatchling"]["character_id"] is not None
    assert state["actors"]["crew"]["stress"] == 1
    verify(load(db, sid))
    db.close()


def test_forced_signature_injury_keeps_healing_and_effects():
    import random

    from lorekit.alien import creatures
    from lorekit.alien.state import initial

    bundle = {
        "collections": {
            "creatures": {"test": {"health": 2, "speed": 1, "skills": {}}},
            "injuries": {
                str(t * 10 + u): {"effects": [{"hook": "movement_full"}], "healing": {"amount": 2, "unit": "day"}}
                for t in range(1, 7)
                for u in range(1, 7)
            },
        }
    }
    state = initial()
    state["actors"]["source"] = creatures.build("test", "Source", bundle)
    target = creatures.build("test", "Target", bundle)
    target["species"] = "human"
    state["actors"]["target"] = target
    state["pending"] = {
        "type": "signature",
        "execution": {
            "actor": "source",
            "target": "target",
            "actions": [{"hook": "critical_injury", "injury": 25, "when": "damage"}],
            "index": 0,
            "hit": True,
            "damage": True,
            "die": 5,
            "choices": {},
        },
    }
    creatures.resume(state, bundle, random.Random(1))
    assert target["injuries"][0]["healing_remaining"] == 2
    assert target["injuries"][0]["effects"] == [{"hook": "movement_full"}]


def test_tail_hold_ends_when_source_dies_or_acts():
    import random

    import pytest

    from lorekit.alien import creatures, scenes
    from lorekit.alien.state import initial
    from lorekit.db import LoreKitError

    bundle = {
        "collections": {
            "creatures": {
                "test": {
                    "health": 2,
                    "speed": 1,
                    "skills": {},
                    "attacks": {
                        str(i): {"id": str(i), "actions": [{"hook": "stress", "value": 1}]} for i in range(1, 7)
                    },
                }
            }
        }
    }
    state = initial()
    for aid in ["source", "target"]:
        state["actors"][aid] = creatures.build("test", aid, bundle)
    scenes.setup(
        state,
        {
            "zones": {"a": {}, "b": {}},
            "borders": [{"a": "a", "b": "b", "open": True}],
            "placements": {"source": "a", "target": "a"},
        },
    )
    state["actors"]["target"]["immobilized_by"] = "source"
    with pytest.raises(LoreKitError, match="tail hold"):
        scenes.move(state, "target", "b", bundle, forced=True)
    state["actors"]["source"]["dead"] = True
    scenes.move(state, "target", "b", bundle, forced=True)
    state["actors"]["source"]["dead"] = False
    scenes.start_combat(state, {"actors": ["source", "target"], "surprise": ["source"]}, random.Random(1))
    creatures.act(
        state, {"actor": "source", "target": "target", "feasible": list(range(1, 7))}, bundle, random.Random(1)
    )
    assert "immobilized_by" not in state["actors"]["target"]
