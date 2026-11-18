"""Original cinematic fixture: stealth through injury, retreat and durable branching."""

from lorekit.alien import content
from lorekit.alien.state import execute, load, verify
from lorekit.db import require_db
from lorekit.support import checkpoint


def test_cinematic_restarts_branches_and_recovers(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    serial = 0

    def act(intent, seed=5):
        nonlocal serial
        revision = load(db, sid)["state"]["revision"]
        serial += 1
        return execute(db, sid, intent, f"cinematic-{serial}", revision, seed)

    def settle():
        for _ in range(50):
            s = load(db, sid)["state"]
            p = s["pending"]
            if p:
                if p["type"] == "check":
                    act(
                        {
                            "op": "check_decide",
                            "actor": p["checks"][p["index"]]["actor"],
                            "decision_id": p["id"],
                            "choice": "accept",
                        }
                    )
                elif p["type"] == "panic_choice" and p["result"] == 2:
                    act({"op": "panic_decide", "actor": p["actor"], "alerted": ["threat"]})
                else:
                    raise AssertionError(p)
            elif s["queue"]:
                act({"op": "queue_continue"})
            else:
                return
        raise AssertionError("Unbounded cinematic decisions")

    def turn(aid):
        for _ in range(20):
            s = load(db, sid)["state"]
            current = s["combat"]["slots"][s["combat"]["index"]]["actors"]
            if aid in current:
                return
            act({"op": "end_turn", "actor": current[0]})
            settle()
        raise AssertionError("Actor never receives a turn")

    b = {
        "profile": "evolved",
        "source": "Original acceptance fixtures",
        "collections": {
            "items": {
                "air": {"page": 1, "capacity": 5, "supply_type": "air", "weight": 0.5},
                "kit": {
                    "page": 1,
                    "weight": 0.25,
                    "single_use": True,
                    "check_modifiers": [{"skill": "medical_aid", "context": "first_aid", "value": 2}],
                },
            },
            "injuries": {
                str(t * 10 + u): {"page": 1, "effects": [], "lethal": True, "interval": "shift"}
                for t in range(1, 7)
                for u in range(1, 7)
            },
        },
    }
    act({"op": "content_import", "hash": content.install(b)})
    act(
        {
            "op": "character_import",
            "actor": "crew",
            "build": {
                "name": "Crew",
                "attributes": dict(strength=5, agility=3, wits=2, empathy=4),
                "inventory": [{"id": "air", "content_id": "air", "equipped": True, "active_air": True}],
            },
        }
    )
    act(
        {
            "op": "character_import",
            "actor": "medic",
            "build": {
                "name": "Medic",
                "attributes": dict(strength=3, agility=3, wits=3, empathy=5),
                "skills": {"medical_aid": 5},
                "inventory": [{"id": "kit", "content_id": "kit"}],
            },
        }
    )
    act(
        {
            "op": "character_import",
            "actor": "threat",
            "build": {"name": "Threat", "kind": "npc", "attributes": dict(strength=3, agility=3, wits=3, empathy=3)},
        }
    )
    act(
        {
            "op": "scene_setup",
            "zones": {"hall": {"cluttered": True}, "room": {"cluttered": True}},
            "borders": [{"a": "hall", "b": "room", "door": True}],
            "placements": {"crew": "hall", "medic": "hall", "threat": "room"},
        }
    )
    act({"op": "stealth_act", "actor": "crew", "group": ["crew", "medic"], "action": "move", "zone": "room"}, 1)
    p = load(db, sid)["state"]["pending"]
    if p:
        assert p["type"] == "detection"
        act({"op": "detection_decide", "actor": p["winner"][0], "decision_id": p["id"], "choice": "reveal"})
    assert load(db, sid)["state"]["combat"]

    act({"op": "check", "actor": "crew", "skill": "comtech"}, 1)
    pending = load(db, sid)["state"]["pending"]
    checkpoint.create_checkpoint(db, sid)
    checkpoint.manual_save(db, sid, "pending")
    act({"op": "check_decide", "actor": "crew", "decision_id": pending["id"], "choice": "accept"})
    checkpoint.create_checkpoint(db, sid)
    checkpoint.manual_save(db, sid, "accepted-future")
    db.commit()
    db.close()
    db = require_db()
    checkpoint.save_load(db, sid, "pending")
    assert load(db, sid)["state"]["pending"] == pending
    act({"op": "check_decide", "actor": "crew", "decision_id": pending["id"], "choice": "push"})
    settle()
    checkpoint.create_checkpoint(db, sid)
    assert db.execute("SELECT COUNT(*) FROM checkpoint_branches WHERE session_id=?", (sid,)).fetchone()[0] >= 2
    # Revert the branch point and restore its future, including the full rules audit.
    before = load(db, sid)
    checkpoint.revert_to_previous(db, sid, 1)
    checkpoint.advance_to_next(db, sid, 1)
    assert load(db, sid) == before

    a = load(db, sid)["state"]["actors"]["crew"]
    act({"op": "stress", "actor": "crew", "amount": 10 - a["stress"], "reason": "Original cinematic horror"})
    act({"op": "panic", "actor": "crew", "reason": "Unnerving discovery"}, 1)
    settle()
    assert 9 in load(db, sid)["state"]["actors"]["crew"]["panic_responses"]
    act({"op": "damage", "actor": "crew", "amount": 4}, 1)
    settle()
    injury = load(db, sid)["state"]["actors"]["crew"]["injuries"][0]
    assert injury["due_unit"] == "shift" and not injury["stabilized"]
    turn("medic")
    act({"op": "approach", "actor": "medic", "target": "crew"})
    act({"op": "recover", "actor": "medic", "target": "crew", "method": "first_aid", "gear": ["kit"]})
    settle()
    assert load(db, sid)["state"]["actors"]["crew"]["injuries"][0]["stabilized"]
    for aid in ["crew", "medic"]:
        turn(aid)
        act({"op": "move", "actor": aid, "zone": "hall"})
        act({"op": "leave_combat", "actor": aid, "route_unclear": True}, 1)
    assert load(db, sid)["state"]["mode"] == "stealth"
    act({"op": "recover", "actor": "crew", "actors": ["crew", "medic"], "method": "rest", "safe": True})
    act({"op": "supply", "actor": "crew", "item": "air"}, 1)
    verify(load(db, sid))
    db.close()
