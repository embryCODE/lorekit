"""Transactional command and rules boundaries for Alien."""

import copy

import pytest

from lorekit.alien.state import execute, load, projection, verify
from lorekit.db import LoreKitError, require_db


def spec(**changes):
    return {
        "name": "Example technician",
        "attributes": {"strength": 3, "agility": 3, "wits": 4, "empathy": 4},
        **changes,
    }


def apply(db, sid, intent, rid, seed=7):
    return execute(db, sid, intent, rid, load(db, sid)["state"]["revision"], seed)


def test_atomic_retries_collisions_and_replay(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    command = {"op": "character_import", "actor": "tech", "build": spec()}
    first = execute(db, sid, command, "create", 0, 0)
    assert execute(db, sid, command, "create", 0, 0) == first
    assert db.execute("SELECT COUNT(*) FROM characters WHERE session_id=?", (sid,)).fetchone()[0] == 1
    with pytest.raises(LoreKitError, match="request_id"):
        execute(db, sid, {**command, "actor": "other"}, "create", 0, 0)
    before = load(db, sid)
    with pytest.raises(LoreKitError, match="Stale"):
        execute(db, sid, {"op": "stress", "actor": "tech", "reason": "horror"}, "stale", 0, 1)
    with pytest.raises(LoreKitError, match="Actor ID"):
        apply(db, sid, command, "duplicate")
    assert load(db, sid) == before
    assert db.execute("SELECT COUNT(*) FROM characters WHERE session_id=?", (sid,)).fetchone()[0] == 1
    verify(before)
    bad = copy.deepcopy(before)
    bad["audit"][0]["digest"] = "tampered"
    with pytest.raises(LoreKitError, match="divergence"):
        verify(bad)
    db.close()


def test_caller_transaction_is_not_committed(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    db.execute("BEGIN")
    apply(db, sid, {"op": "character_import", "actor": "tech", "build": spec()}, "create")
    db.rollback()
    assert load(db, sid)["state"]["actors"] == {}
    db.close()


def test_pending_push_restart_and_revert(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    apply(db, sid, {"op": "character_import", "actor": "tech", "build": spec()}, "create")
    result = apply(db, sid, {"op": "check", "actor": "tech", "skill": "observation"}, "check", seed=2)
    pending = copy.deepcopy(result["pending"])
    db.close()
    db = require_db()
    assert load(db, sid)["state"]["pending"] == pending
    old = pending["checks"][0]["roll"]
    result = apply(
        db, sid, {"op": "check_decide", "actor": "tech", "decision_id": pending["id"], "choice": "push"}, "push", seed=3
    )
    state = load(db, sid)["state"]
    assert state["actors"]["tech"]["stress"] >= 1
    rolled = (state["pending"] or result)["checks"][0]["roll"]
    for index, die in enumerate(old["base"]):
        if die == 6:
            assert rolled["base"][index] == 6
    verify(load(db, sid))
    db.close()


def test_passive_roll_suppresses_response_and_push(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    apply(db, sid, {"op": "character_import", "actor": "tech", "build": spec(stress=6)}, "create")
    result = apply(
        db, sid, {"op": "check", "actor": "tech", "skill": "observation", "passive": True}, "passive", seed=1
    )
    assert result["type"] == "check_complete"
    assert 1 in result["checks"][0]["roll"]["stress"]
    assert result["checks"][0]["events"] == []
    assert load(db, sid)["state"]["actors"]["tech"]["stress_responses"] == []
    db.close()


def test_private_synthetic_is_not_exposed(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    apply(
        db,
        sid,
        {
            "op": "character_import",
            "actor": "secret",
            "build": spec(species="synthetic", secret_identity=True, private={"agenda": "private"}),
        },
        "create",
    )
    state = load(db, sid)["state"]
    public = projection(state)
    assert public["actors"]["secret"] == {"id": "secret", "name": "Example technician", "zone": None}
    apply(db, sid, {"op": "stress", "actor": "secret", "amount": 10, "reason": "test"}, "stress")
    assert load(db, sid)["state"]["actors"]["secret"]["stress"] == 0
    assert "private" not in str(public)
    db.close()


def test_checkpoint_load_and_branch_restore_pending_decisions(make_session):
    from lorekit.support import checkpoint

    sid = make_session(system="alien-evolved")
    db = require_db()
    apply(db, sid, {"op": "character_import", "actor": "tech", "build": spec()}, "create")
    apply(db, sid, {"op": "check", "actor": "tech", "skill": "observation"}, "roll")
    checkpoint.create_checkpoint(db, sid)
    checkpoint.manual_save(db, sid, "pending-check")
    original = load(db, sid)
    pending = original["state"]["pending"]
    apply(db, sid, {"op": "check_decide", "actor": "tech", "decision_id": pending["id"], "choice": "accept"}, "accept")
    apply(db, sid, {"op": "stress", "actor": "tech", "amount": 2, "reason": "temporary future"}, "future")
    checkpoint.create_checkpoint(db, sid)
    checkpoint.save_load(db, sid, "pending-check")
    assert load(db, sid) == original
    apply(
        db,
        sid,
        {"op": "check_decide", "actor": "tech", "decision_id": pending["id"], "choice": "push"},
        "alternate",
        seed=3,
    )
    checkpoint.create_checkpoint(db, sid)
    verify(load(db, sid))
    assert all(e["request_id"] != "future" for e in load(db, sid)["audit"])
    db.close()


def test_opposed_pc_push_order_and_story_point_cannot_be_spent_twice(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    for aid in ("active", "passive"):
        apply(db, sid, {"op": "character_import", "actor": aid, "build": spec(story_points=1)}, f"create-{aid}")
    result = apply(
        db,
        sid,
        {"op": "check", "actor": "active", "skill": "comtech", "opponent": {"actor": "passive", "skill": "comtech"}},
        "opposed",
        seed=1,
    )
    p = result["pending"]
    before = load(db, sid)
    with pytest.raises(LoreKitError, match="not due"):
        apply(db, sid, {"op": "check_decide", "actor": "passive", "decision_id": p["id"], "choice": "push"}, "early")
    assert load(db, sid) == before
    active_roll = p["checks"][0]["roll"]["base"]
    index = next(i for i, d in enumerate(active_roll) if d != 6)
    apply(
        db,
        sid,
        {"op": "check_decide", "actor": "active", "decision_id": p["id"], "choice": "story_point", "die": index},
        "story",
    )
    assert load(db, sid)["state"]["actors"]["active"]["story_points"] == 0
    with pytest.raises(LoreKitError, match="story-point"):
        apply(
            db,
            sid,
            {"op": "check_decide", "actor": "active", "decision_id": p["id"], "choice": "story_point", "die": index},
            "story-again",
        )
    apply(
        db, sid, {"op": "check_decide", "actor": "active", "decision_id": p["id"], "choice": "accept"}, "accept-active"
    )
    pending = load(db, sid)["state"]["pending"]
    assert pending["checks"][pending["index"]]["actor"] == "passive"
    verify(load(db, sid))
    db.close()


def test_version_mismatch_fails_closed(make_session):
    import json

    from lorekit.alien.state import KEY, canonical

    sid = make_session(system="alien-evolved")
    db = require_db()
    apply(db, sid, {"op": "character_import", "actor": "tech", "build": spec()}, "create")
    record = load(db, sid)
    record["state"]["version"] = 999
    db.execute("UPDATE session_meta SET value=? WHERE session_id=? AND key=?", (canonical(record), sid, KEY))
    db.commit()
    with pytest.raises(LoreKitError, match="Incompatible"):
        load(db, sid)
    db.close()


def test_due_death_check_survives_restart_and_cannot_push(make_session):
    from lorekit.alien.content import install

    sid = make_session(system="alien-evolved")
    db = require_db()
    # Original complete numerical fixture whose every result has a shift deadline.
    bundle = {
        "profile": "evolved-beta2",
        "source": "Original fixtures",
        "collections": {
            "injuries": {
                str(t * 10 + u): {"page": 1, "effects": [], "lethal": True, "interval": "shift"}
                for t in range(1, 7)
                for u in range(1, 7)
            }
        },
    }
    apply(db, sid, {"op": "content_import", "hash": install(bundle)}, "content")
    apply(db, sid, {"op": "character_import", "actor": "tech", "build": spec(story_points=1)}, "create")
    apply(db, sid, {"op": "damage", "actor": "tech", "amount": 5}, "damage")
    result = apply(db, sid, {"op": "time_advance", "unit": "shift"}, "shift", seed=1)
    if result["pending"]:
        p = result["pending"]
        db.close()
        db = require_db()
        assert load(db, sid)["state"]["pending"] == p
        with pytest.raises(LoreKitError, match="cannot be pushed"):
            apply(
                db, sid, {"op": "check_decide", "actor": "tech", "decision_id": p["id"], "choice": "push"}, "push-death"
            )
        assert p["checks"][0]["roll"]["stress"] == []
    verify(load(db, sid))
    db.close()


def test_round_injury_rolls_on_actors_turn_not_round_boundary(make_session):
    from lorekit.alien.content import install

    sid = make_session(system="alien-evolved")
    db = require_db()
    bundle = {
        "profile": "evolved-beta2",
        "source": "Original fixtures",
        "collections": {
            "injuries": {
                str(t * 10 + u): {"page": 1, "effects": [], "lethal": True, "interval": "round"}
                for t in range(1, 7)
                for u in range(1, 7)
            }
        },
    }
    apply(db, sid, {"op": "content_import", "hash": install(bundle)}, "content")
    for aid in ("a", "b"):
        apply(db, sid, {"op": "character_import", "actor": aid, "build": spec(kind="npc")}, f"create-{aid}")
    apply(db, sid, {"op": "scene_setup", "zones": {"room": {}}, "placements": {"a": "room", "b": "room"}}, "scene")
    result = apply(db, sid, {"op": "combat_start", "actors": ["a", "b"]}, "combat")
    first = result["combat"]["slots"][0]["actors"][0]
    second = "b" if first == "a" else "a"
    apply(db, sid, {"op": "damage", "actor": first, "amount": 4}, "injure")
    apply(db, sid, {"op": "end_turn", "actor": first}, "end-first", seed=5)
    a = load(db, sid)["state"]["actors"][first]
    assert a["injuries"][0]["successes"] == 1
    assert load(db, sid)["state"]["clocks"]["round"] == 0
    apply(db, sid, {"op": "end_turn", "actor": second}, "end-second", seed=5)
    assert load(db, sid)["state"]["actors"][first]["injuries"][0]["successes"] == 1
    verify(load(db, sid))
    db.close()


def test_public_shape_does_not_fingerprint_secret_identity(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    apply(db, sid, {"op": "character_import", "actor": "human", "build": spec()}, "human")
    apply(
        db,
        sid,
        {"op": "character_import", "actor": "cover", "build": spec(species="synthetic", secret_identity=True)},
        "synthetic",
    )
    public = projection(load(db, sid)["state"])
    assert set(public["actors"]["human"]) == set(public["actors"]["cover"])
    apply(
        db,
        sid,
        {"op": "disclose", "actor": "human", "fields": ["health"], "reason": "Player publicly checks their wound"},
        "disclose",
    )
    assert projection(load(db, sid)["state"])["actors"]["human"]["health"] == 3
    with pytest.raises(LoreKitError, match="revelation"):
        apply(
            db,
            sid,
            {"op": "disclose", "actor": "cover", "fields": ["species"], "reason": "Attempt premature disclosure"},
            "bad-reveal",
        )
    verify(load(db, sid))
    db.close()


def test_narrative_success_budget_cannot_be_spent_twice(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    apply(
        db,
        sid,
        {"op": "character_import", "actor": "expert", "build": spec(kind="npc", skills={"observation": 5})},
        "create",
    )
    rolled = apply(db, sid, {"op": "check", "actor": "expert", "skill": "observation"}, "check", seed=5)
    assert rolled["benefit_budget"]["expert"] >= 1
    intent = {
        "op": "check_benefits",
        "actor": "expert",
        "check_id": rolled["check_id"],
        "benefits": ["Identify the next clue"],
    }
    apply(db, sid, intent, "claim")
    before = load(db, sid)
    with pytest.raises(LoreKitError, match="distinct"):
        apply(db, sid, intent, "duplicate")
    assert load(db, sid) == before
    verify(load(db, sid))
    db.close()


def test_snapshot_tampering_fails_before_next_mutation(make_session):
    from lorekit.alien.state import KEY, canonical

    sid = make_session(system="alien-evolved")
    db = require_db()
    apply(db, sid, {"op": "character_import", "actor": "crew", "build": spec()}, "create")
    record = load(db, sid)
    record["state"]["actors"]["crew"]["health"] = 1
    db.execute("UPDATE session_meta SET value=? WHERE session_id=? AND key=?", (canonical(record), sid, KEY))
    db.commit()
    with pytest.raises(LoreKitError, match="digest mismatch"):
        execute(
            db, sid, {"op": "stress", "actor": "crew", "amount": 1, "reason": "Must not commit"}, "after-tamper", 1, 1
        )
    assert db.execute("SELECT value FROM session_meta WHERE session_id=? AND key=?", (sid, KEY)).fetchone()[
        0
    ] == canonical(record)
    db.close()


def test_broken_self_recovery_time_retry_and_replay(make_session):
    sid = make_session(system="alien-evolved")
    db = require_db()
    apply(db, sid, {"op": "character_import", "actor": "tech", "build": spec(health=0)}, "import-broken")
    advanced = execute(db, sid, {"op": "time_advance", "unit": "stretch", "amount": 1}, "elapsed", 1, 5)
    state = load(db, sid)["state"]
    assert state["actors"]["tech"]["health"] == 1
    assert execute(db, sid, {"op": "time_advance", "unit": "stretch", "amount": 1}, "elapsed", 1, 5) == advanced
    assert load(db, sid)["state"] == state
    verify(load(db, sid))
    db.close()
