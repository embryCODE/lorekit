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
        "profile": "evolved",
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
        "profile": "evolved",
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
    state["actors"]["target"]["zone"] = "a"
    scenes.start_combat(state, {"actors": ["source", "target"], "surprise": ["source"]}, random.Random(1))
    creatures.act(
        state, {"actor": "source", "target": "target", "feasible": list(range(1, 7))}, bundle, random.Random(1)
    )
    assert "immobilized_by" not in state["actors"]["target"]


def test_signature_panic_choice_precedes_following_resistance_check():
    from lorekit.alien import characters, creatures
    from lorekit.alien.state import initial

    class CountingDie:
        calls = 0

        def randint(self, low, high):
            self.calls += 1
            return 2

    b = {"collections": {"creatures": {"test": {"health": 2, "speed": 1, "skills": {}}}}}
    s = initial()
    s["actors"]["source"] = creatures.build("test", "Source", b)
    s["actors"]["target"] = characters.build(
        {"name": "Target", "attributes": dict(strength=3, agility=3, wits=3, empathy=3), "stress": 4}, b, imported=True
    )
    s["pending"] = {
        "type": "signature",
        "execution": {
            "actor": "source",
            "target": "target",
            "actions": [{"hook": "panic"}, {"hook": "resist_damage", "amount": 1}],
            "index": 0,
            "hit": False,
            "damage": False,
            "die": 3,
            "choices": {},
        },
    }
    rng = CountingDie()
    creatures.resume(s, b, rng)
    assert s["pending"]["type"] == "panic_choice"
    assert s["pending"]["result"] == 3
    assert rng.calls == 1
    assert s["queue"][0]["type"] == "signature_resume"


def test_broken_outcomes_and_last_breath_second_hit():
    import copy
    import random

    from lorekit.alien import characters, creatures, effects, scenes, survival
    from lorekit.alien.engine import transition
    from lorekit.alien.state import initial

    class FixedDie:
        def __init__(self, value):
            self.value = value

        def randint(self, low, high):
            return self.value

    b = {"collections": {"creatures": {"test": {"health": 4, "speed": 2, "skills": {}}}}}
    for value in range(1, 7):
        s = initial()
        s["actors"]["source"] = creatures.build("test", "Specimen", b)
        s["actors"]["source"]["health"] = 0
        s["actors"]["crew"] = characters.build(
            {"name": "Crew", "attributes": dict(strength=3, agility=3, wits=3, empathy=3)}, b, imported=True
        )
        scenes.setup(
            s,
            {
                "zones": {"a": {}, "b": {}, "c": {}},
                "borders": [{"a": "a", "b": "b", "open": True}, {"a": "b", "b": "c", "open": True}],
                "placements": {"source": "a", "crew": "a"},
            },
        )
        creatures.broken(s, "source", FixedDie(value), b["collections"]["creatures"]["test"])
        a = s["actors"]["source"]
        if value == 1:
            scenes.start_combat(s, {"actors": ["source", "crew"], "surprise": ["source"]}, random.Random(1))
            transition(s, {"op": "begin_turn", "actor": "source"}, random.Random(1))
            assert a["health"] == 1
        elif value == 2:
            assert a["speed"] == 1 and a["health"] == 1
            scenes.start_combat(s, {"actors": ["source", "crew"], "surprise": ["source"]}, random.Random(1))
            transition(s, {"op": "begin_turn", "actor": "source"}, random.Random(1))
            assert s["pending"]["type"] == "creature_escape"
        elif value == 3:
            effects.drain(s, random.Random(1), bundle=b)
            assert s["pending"]["type"] == "creature_escape"
            creatures.escape_decide(
                s, {"actor": "source", "path": ["b", "c"], "route_unclear": True}, b, random.Random(1)
            )
            assert a["escaped"]
        elif value == 4:
            assert a["last_breath"] and not a["dead"]
            survival.damage(s, "source", 1, b, FixedDie(1))
            assert a["dead"]
        else:
            assert a["dead"]
    for early, mode in [(True, False), (False, True)]:
        s = initial()
        s["options"]["aliens_mode"] = mode
        s["actors"]["source"] = creatures.build("test", "Specimen", b)
        creatures.broken(s, "source", FixedDie(1), {"early_stage": early})
        assert s["actors"]["source"]["dead"]


def test_attached_parasite_moves_with_host_and_stasis_pauses_its_clock():
    import random

    from lorekit.alien import characters, creatures, effects, scenes, survival
    from lorekit.alien.state import initial, validate_snapshot

    b = {
        "collections": {
            "creatures": {
                "parasite": {"stage": "facehugger", "health": 2, "speed": 1, "skills": {}, "birth": "juvenile"},
                "juvenile": {"health": 2, "speed": 1, "skills": {}, "matures": "adult"},
                "adult": {"health": 4, "speed": 1, "skills": {}},
            }
        }
    }
    s = initial()
    s["actors"]["parasite"] = creatures.build("parasite", "Parasite", b)
    s["actors"]["host"] = characters.build(
        {"name": "Host", "kind": "npc", "attributes": dict(strength=3, agility=3, wits=3, empathy=3)}, b, imported=True
    )
    scenes.setup(
        s,
        {
            "zones": {"a": {}, "b": {}},
            "borders": [{"a": "a", "b": "b", "open": True}],
            "placements": {"parasite": "a", "host": "a"},
        },
    )
    s["actors"]["parasite"]["adjacent"] = ["host"]
    s["actors"]["host"]["adjacent"] = ["parasite"]
    s["pending"] = {
        "type": "signature",
        "execution": {
            "actor": "parasite",
            "target": "host",
            "actions": [{"hook": "facehug"}],
            "index": 0,
            "hit": True,
            "damage": False,
            "die": 6,
            "choices": {},
        },
    }
    creatures.resume(s, b, random.Random(1))
    scenes.move(s, "host", "b", b, forced=True)
    assert s["actors"]["parasite"]["zone"] == "b"
    assert s["actors"]["parasite"]["attached_to"] == "host"
    validate_snapshot(s)
    creatures.lifecycle(
        s, {"actor": "host", "kind": "stasis", "active": True, "reason": "GM confirms cryostorage"}, b, random.Random(1)
    )
    for _ in range(2):
        survival.advance_time(s, "shift", 1)
        effects.drain(s, random.Random(1), bundle=b)
    assert s["actors"]["host"]["facehug"]["stage"] == "attached"
    creatures.lifecycle(
        s, {"actor": "host", "kind": "stasis", "active": False, "reason": "GM confirms awakening"}, b, random.Random(1)
    )
    assert s["actors"]["host"]["facehug"]["shift_due"] == 3
    survival.advance_time(s, "shift", 1)
    effects.drain(s, random.Random(1), bundle=b)
    assert s["actors"]["host"]["facehug"]["stage"] == "incubating"
    assert "attached_to" not in s["actors"]["parasite"]
    creatures.lifecycle(
        s,
        {"actor": "host", "kind": "deadline", "shift": 6, "reason": "GM chooses slower gestation"},
        b,
        random.Random(1),
    )
    creatures.lifecycle(
        s, {"actor": "host", "kind": "resolve", "reason": "GM explicitly advances the birth"}, b, random.Random(1)
    )
    assert s["actors"]["host"]["dead"] and "host-burster" in s["actors"]
    survival.advance_time(s, "shift", 1)
    effects.drain(s, random.Random(1), bundle=b)
    assert s["actors"]["host-burster"]["content_id"] == "adult"
    validate_snapshot(s)


def test_facehugger_automatically_attaches_to_injury_immobilized_host():
    import random

    from lorekit.alien import creatures, scenes
    from lorekit.alien.characters import build
    from lorekit.alien.state import initial

    b = {
        "collections": {
            "creatures": {
                "parasite": {
                    "stage": "facehugger",
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
    s = initial()
    s["actors"]["parasite"] = creatures.build("parasite", "Example", b)
    host = s["actors"]["host"] = build(
        {"name": "Host", "attributes": dict(strength=3, agility=3, wits=3, empathy=3)}, b, imported=True
    )
    host["injuries"] = [
        {"id": "example", "effects": [{"hook": "immobile"}], "interval": None, "lethal": False, "stabilized": True}
    ]
    scenes.setup(s, {"zones": {"room": {}}, "placements": {"parasite": "room", "host": "room"}})
    scenes.approach(s, "parasite", "host", b, paid=True)
    scenes.start_combat(s, {"actors": ["parasite", "host"]}, random.Random(1))
    creatures.act(s, {"actor": "parasite", "target": "host", "feasible": [1]}, b, random.Random(1), immediate=True)
    assert "facehug" in host
    assert host["unconscious"] and s["actors"]["parasite"]["attached_to"] == "host"


def test_signature_instant_kill_triggers_witness_panic_after_nonbreaking_hit():
    import random

    from lorekit.alien import creatures, scenes
    from lorekit.alien.characters import build
    from lorekit.alien.state import initial

    b = {"collections": {"creatures": {"hunter": {"health": 3, "speed": 1, "skills": {}}}}}
    s = initial()
    s["actors"]["hunter"] = creatures.build("hunter", "Hunter", b)
    for aid in ("host", "witness"):
        s["actors"][aid] = build(
            {"name": aid, "attributes": dict(strength=3, agility=3, wits=3, empathy=3)}, b, imported=True
        )
    scenes.setup(s, {"zones": {"room": {}}, "placements": {aid: "room" for aid in s["actors"]}})
    s["pending"] = {
        "type": "signature",
        "execution": {
            "actor": "hunter",
            "target": "host",
            "actions": [{"hook": "instant_kill", "when": "damage"}],
            "index": 0,
            "hit": True,
            "damage": True,
            "die": 6,
            "choices": {},
        },
    }
    s["actors"]["host"]["panic_responses"] = [5]
    creatures.resume(s, b, random.Random(1))
    assert s["actors"]["host"]["dead"] and s["actors"]["host"]["health"] == 0
    assert s["actors"]["host"]["panic_responses"] == []
    assert "highest_panic" in s["actors"]["witness"]
