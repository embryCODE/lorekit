"""Talent decisions use original fixtures, including resource and time boundaries."""

import pytest

from lorekit.alien import characters, content, talent_actions
from lorekit.alien.state import execute, load, verify
from lorekit.db import LoreKitError, require_db


def test_company_resources_limits_and_analysis_answer_survive_replay(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    bundle = {
        "profile": "evolved",
        "source": "Original talent fixtures",
        "collections": {
            "talents": {key: {"page": 1, "effects": [{"hook": key}]} for key in ("company_resources", "analysis")},
            "items": {"sample": {"page": 1, "weight": 0.5}},
        },
    }
    revision = 0

    def act(intent, seed=5):
        nonlocal revision
        result = execute(db, sid, intent, f"step-{revision}", revision, seed)
        revision = result["revision"]
        return result

    act({"op": "content_import", "hash": content.install(bundle)})
    act(
        {
            "op": "character_import",
            "actor": "scientist",
            "build": {
                "name": "Example",
                "kind": "npc",
                "attributes": dict(strength=3, agility=3, wits=5, empathy=3),
                "skills": {"observation": 5},
                "talents": ["company_resources", "analysis"],
            },
        }
    )
    command = {
        "op": "talent_use",
        "actor": "scientist",
        "talent": "company_resources",
        "act_id": "act-one",
        "content_id": "sample",
        "item": "borrowed",
    }
    act(command)
    before = load(db, sid)
    with pytest.raises(LoreKitError, match="already used"):
        act({**command, "item": "another"})
    assert load(db, sid) == before
    out = act(
        {
            "op": "talent_use",
            "actor": "scientist",
            "talent": "analysis",
            "object": "artifact-a",
            "stretch_completed": True,
        }
    )
    assert out["pending"]["type"] == "talent_answer"
    verify(load(db, sid))
    while load(db, sid)["state"]["pending"]:
        pending = load(db, sid)["state"]["pending"]
        question = next(q for q in sorted(talent_actions.QUESTIONS) if q not in pending["asked"])
        act(
            {
                "op": "talent_answer",
                "actor": "scientist",
                "question": question,
                "answer": "The GM records an observation.",
            }
        )
    verify(load(db, sid))
    db.close()


def test_resilient_does_not_add_stress_or_offer_push():
    import random

    from lorekit.alien import survival
    from lorekit.alien.state import initial

    bundle = {"collections": {"talents": {"shield": {"effects": [{"hook": "resilient"}]}}}}
    s = initial()
    s["actors"]["a"] = characters.build(
        {
            "name": "Example",
            "attributes": dict(strength=5, agility=5, wits=3, empathy=3),
            "talents": ["shield"],
            "stress": 3,
        },
        bundle,
        imported=True,
    )
    out = survival.damage(s, "a", 1, bundle, random.Random(5))
    assert len(out[0]["resistance_dice"]) == 5
    assert s["actors"]["a"]["stress"] == 3
    assert s["pending"] is None


def test_nurse_capacity_and_day_are_explicit():
    import random

    from lorekit.alien.state import initial

    bundle = {"collections": {"talents": {"care": {"effects": [{"hook": "nurse"}]}}}}
    s = initial()
    for aid in ("medic", "patient"):
        s["actors"][aid] = characters.build(
            {
                "name": aid,
                "attributes": dict(strength=3, agility=3, wits=3, empathy=3),
                "skills": {"medical_aid": 1},
                "talents": ["care"],
            },
            bundle,
            imported=True,
        )
        s["actors"][aid]["zone"] = "room"
    with pytest.raises(LoreKitError, match="completed shift"):
        talent_actions.act(s, {"actor": "medic", "talent": "nurse", "patients": ["patient"]}, bundle, random.Random(1))
    talent_actions.act(
        s,
        {"actor": "medic", "talent": "nurse", "patients": ["patient"], "shift_completed": True},
        bundle,
        random.Random(1),
    )
    assert s["actors"]["patient"]["nursed_until_day"] == 1


def test_initiative_choices_reserve_unique_cards_across_restart(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    b = {
        "profile": "evolved",
        "source": "Original initiative fixtures",
        "collections": {
            "talents": {key: {"page": 1, "effects": [{"hook": key}]} for key in ("fast_reflexes", "combat_veteran")}
        },
    }
    revision = 0

    def act(intent):
        nonlocal revision
        out = execute(db, sid, intent, f"initiative-{revision}", revision, 5)
        revision = out["revision"]
        return out

    act({"op": "content_import", "hash": content.install(b)})
    for aid in ("first", "second"):
        act(
            {
                "op": "character_import",
                "actor": aid,
                "build": {
                    "name": aid,
                    "attributes": dict(strength=3, agility=3, wits=3, empathy=3),
                    "talents": ["fast_reflexes", "combat_veteran"],
                },
            }
        )
    act({"op": "scene_setup", "zones": {"room": {}}, "placements": {"first": "room", "second": "room"}})
    act({"op": "combat_start", "actors": ["first", "second"]})
    for _ in range(2):
        p = load(db, sid)["state"]["pending"]
        verify(load(db, sid))
        assert p["choice"]["kind"] == "fast"
        act({"op": "initiative_decide", "actor": p["choice"]["actors"][0], "card": p["choice"]["cards"][0]})
    cards = {s["actors"][0]: s["card"] for s in load(db, sid)["state"]["combat"]["slots"]}
    assert len(set(cards.values())) == 2
    for _ in range(2):
        s = load(db, sid)["state"]
        act({"op": "end_turn", "actor": s["combat"]["slots"][s["combat"]["index"]]["actors"][0]})
    for _ in range(2):
        p = load(db, sid)["state"]["pending"]
        assert p["choice"]["kind"] == "keep"
        act({"op": "initiative_decide", "actor": p["choice"]["actors"][0], "keep": True})
    assert cards == {s["actors"][0]: s["card"] for s in load(db, sid)["state"]["combat"]["slots"]}
    verify(load(db, sid))
    db.close()


def test_lucky_injury_choices_keep_both_rolls_and_do_not_apply_unselected():
    import random

    from lorekit.alien import effects, survival
    from lorekit.alien.state import initial

    b = {
        "collections": {
            "talents": {"choice": {"effects": [{"hook": "lucky"}]}},
            "injuries": {str(t * 10 + u): {"effects": [], "lethal": False} for t in range(1, 7) for u in range(1, 7)},
        }
    }
    s = initial()
    s["actors"]["a"] = characters.build(
        {"name": "A", "attributes": dict(strength=3, agility=3, wits=3, empathy=3), "talents": ["choice"]},
        b,
        imported=True,
    )
    survival.injury(s, "a", b, random.Random(1))
    assert not s["actors"]["a"]["injuries"]
    effects.drain(s, random.Random(2), bundle=b)
    p = s["pending"]
    assert p["type"] == "lucky_injury" and len(p["dice"]) == 2
    survival.injury_decide(s, {"actor": "a", "choice": 1}, b, random.Random(3))
    assert s["actors"]["a"]["injuries"][0]["id"] == str(p["values"][1])


@pytest.mark.parametrize("species,health", [("human", 5), ("synthetic", 0)])
def test_bodyguard_uses_remaining_interrupt_before_damage(species, health):
    import random

    from lorekit.alien import combat, scenes
    from lorekit.alien.state import initial

    b = {"collections": {"talents": {"guard": {"effects": [{"hook": "bodyguard"}]}}}}
    s = initial()
    for aid in ("attacker", "target", "guard"):
        s["actors"][aid] = characters.build(
            {
                "name": aid,
                "attributes": dict(strength=5, agility=5, wits=3, empathy=3),
                "talents": ["guard"] if aid == "guard" else [],
            },
            b,
            imported=True,
        )
    s["actors"]["guard"].update(species=species, health=health)
    scenes.setup(s, {"zones": {"room": {}}, "placements": {aid: "room" for aid in s["actors"]}})
    scenes.start_combat(s, {"actors": list(s["actors"]), "surprise": ["attacker"]}, random.Random(1))
    scenes.approach(s, "attacker", "target")
    combat.declare(s, {"actor": "attacker", "target": "target"}, b)
    attack = s["pending"]["attack"]
    s["pending"] = None
    result = {"successes": [1], "checks": [{"pushes": 0}], "continuation": {"kind": "attack", "attack": attack}}
    out = combat.finish(s, result, b, random.Random(2))
    assert out["type"] == "hit_reaction"
    combat.hit_decide(s, {"actor": "guard", "choice": "bodyguard"}, b, random.Random(3))
    assert s["actors"]["target"]["health"] == 5
    assert s["actors"]["guard"]["health"] == max(0, health - 1)
    assert s["combat"]["budgets"]["guard"]["quick"] == 1


def test_quick_shot_winner_can_abort_the_other_shot_but_ties_fire_both():
    import random

    from lorekit.alien import combat
    from lorekit.alien.state import initial

    def scenario(scores):
        b = {
            "collections": {
                "items": {"test": {"capacity": 1, "supply_type": "ammo", "damage": 3}},
                "injuries": {
                    str(t * 10 + u): {"effects": [], "lethal": False} for t in range(1, 7) for u in range(1, 7)
                },
            }
        }
        s = initial()
        for aid in ("a", "b"):
            s["actors"][aid] = characters.build(
                {
                    "name": aid,
                    "kind": "npc",
                    "attributes": dict(strength=3, agility=3, wits=3, empathy=3),
                    "inventory": [{"id": "gun", "content_id": "test", "equipped": True}],
                },
                b,
                imported=True,
            )
        attacks = [
            {
                "actor": aid,
                "target": other,
                "weapon": "gun",
                "definition": {"damage": 3},
                "special": None,
                "skill": "ranged_combat",
                "conserve": False,
                "full_auto": False,
                "burst": 1,
                "weak_spot": False,
            }
            for aid, other in [("a", "b"), ("b", "a")]
        ]
        result = {
            "successes": scores,
            "checks": [{"pushes": 0}, {"pushes": 0}],
            "continuation": {"kind": "quick_shot", "attacks": attacks},
        }
        combat.finish_quick_shot(s, result, b, random.Random(1))
        return s

    s = scenario([1, 2])
    assert s["actors"]["a"]["health"] == 0
    assert s["actors"]["b"]["health"] == 3
    assert s["actors"]["a"]["inventory"][0]["supply"] == 1
    s = scenario([1, 1])
    assert s["actors"]["a"]["health"] == s["actors"]["b"]["health"] == 0
    assert s["actors"]["a"]["inventory"][0]["supply"] == s["actors"]["b"]["inventory"][0]["supply"] == 0


def test_initiative_result_exposes_resumed_panic_not_a_stale_empty_pending(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    b = {
        "profile": "evolved",
        "source": "Original interrupted initiative fixture",
        "collections": {"talents": {"quick": {"page": 1, "effects": [{"hook": "fast_reflexes"}]}}},
    }
    serial = 0

    def act(intent, seed=1):
        nonlocal serial
        revision = load(db, sid)["state"]["revision"]
        serial += 1
        return execute(db, sid, intent, f"resume-{serial}", revision, seed)

    act({"op": "content_import", "hash": content.install(b)})
    for aid, kind in [("a", "pc"), ("b", "npc")]:
        act(
            {
                "op": "character_import",
                "actor": aid,
                "build": {
                    "name": aid,
                    "kind": kind,
                    "attributes": dict(strength=3, agility=3, wits=3, empathy=3),
                    "talents": ["quick"] if aid == "a" else [],
                },
            }
        )
    act({"op": "scene_setup", "zones": {"room": {}}, "placements": {"a": "room", "b": "room"}})
    act({"op": "stress", "actor": "a", "amount": 12, "reason": "Test fear"})
    act({"op": "panic", "actor": "a", "reason": "Test horror"})
    out = act({"op": "panic_decide", "actor": "a", "target": "b", "approach": True})
    assert out["pending"]["type"] == "initiative"
    card = out["pending"]["choice"]["cards"][0]
    out = act({"op": "initiative_decide", "actor": "a", "card": card})
    assert out["pending"]["type"] == "panic_choice"
    assert out["pending"] == load(db, sid)["state"]["pending"]
    verify(load(db, sid))
    db.close()


def test_hardened_uses_one_health_per_selection_and_caps_at_three():
    b = {
        "collections": {
            "talents": {"hardened": {"max_selections": 3, "effects": [{"hook": "health_bonus", "value": 1}]}}
        }
    }
    spec = {"name": "Example", "attributes": dict(strength=3, agility=4, wits=3, empathy=3)}
    for count in range(4):
        a = characters.build({**spec, "talents": ["hardened"] * count}, b, imported=True)
        assert a["health_max"] == 4 + count
    with pytest.raises(LoreKitError, match="selection limit"):
        characters.build({**spec, "talents": ["hardened"] * 4}, b, imported=True)


def test_inquisitive_only_extends_wits_pushes():
    import random

    from lorekit.alien import checks
    from lorekit.alien.state import initial

    b = {"collections": {"talents": {"inquisitive": {"effects": [{"hook": "push_attribute", "attribute": "wits"}]}}}}
    s = initial()
    a = characters.build(
        {"name": "Example", "attributes": dict(strength=3, agility=3, wits=3, empathy=3), "talents": ["inquisitive"]},
        b,
        imported=True,
    )
    s["actors"]["crew"] = a
    for skill, extra in (("observation", True), ("mobility", False), ("stamina", False), ("command", False)):
        check = checks.roll(s, {"actor": "crew", "skill": skill}, b, random.Random(1))
        check["pushes"] = 1
        assert checks.permission(a, check, b) is extra
        check["pushes"] = 2
        assert not checks.permission(a, check, b)


def test_fast_reflexes_does_not_give_ambusher_priority_on_second_speed_slot():
    import random

    from lorekit.alien import creatures, initiative, scenes
    from lorekit.alien.state import initial

    b = {
        "collections": {
            "talents": {"fast": {"effects": [{"hook": "fast_reflexes"}]}},
            "creatures": {"hunter": {"health": 3, "speed": 2, "skills": {}}},
        }
    }
    s = initial()
    s["actors"]["crew"] = characters.build(
        {"name": "Crew", "attributes": dict(strength=3, agility=3, wits=3, empathy=3), "talents": ["fast"]},
        b,
        imported=True,
    )
    s["actors"]["hunter"] = creatures.build("hunter", "Hunter", b)
    scenes.setup(s, {"zones": {"room": {}}, "placements": {"crew": "room", "hunter": "room"}})
    scenes.start_combat(s, {"actors": ["hunter", "crew"], "surprise": ["hunter"]}, random.Random(1), b)
    pending = s["pending"]
    assert pending["slots"] == [{"actors": ["hunter"], "card": 1}]
    initiative.decide(s, {"actor": "crew", "card": pending["choice"]["cards"][0]}, b, random.Random(1))
    cards = [slot["card"] for slot in s["combat"]["slots"]]
    assert len(cards) == len(set(cards)) == 3
    assert sum(slot["actors"] == ["hunter"] for slot in s["combat"]["slots"]) == 2
