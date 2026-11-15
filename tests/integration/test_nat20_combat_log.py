"""Nat20 combat persisted as an intent log survives LoreKit restarts and checkpoints."""

import json
import time

import pytest

pytest.importorskip("dnd5e_engine")
pytest.importorskip("sqlite_vec")

from lorekit.backends import nat20  # noqa: E402
from lorekit.tools.narrative import manual_save, save_load, turn_revert, turn_save  # noqa: E402

START = {
    "seed": 3,
    "grid": [8, 8],
    "party": [
        {
            "build": {
                "species_slug": "human",
                "class_slug": "fighter",
                "level": 3,
                "ability_scores": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
            },
            "instance": {
                "entity_id": "char:valeros",
                "name": "Valeros",
                "hp_current": 28,
                "hp_max": 28,
                "ac": 16,
                "initiative": 1,
                "zone_id": [0, 0],
            },
        }
    ],
    "encounter": [
        {"entity_id": f"mon:goblin{i}", "slug": "goblin-warrior", "hp": 7, "init": 20 - i, "zone_id": [3, i]}
        for i in range(2)
    ],
}


def _db():
    from lorekit.db import get_db

    return get_db()


def _step(db, sid):
    """Monsters act on their own; the PC just Dodges, so monsters do the damage."""
    cur = nat20.status(db, sid)["current"]
    if cur.startswith("char:"):
        return nat20.act(db, sid, cur, {"intent_type": "dodge"})
    return nat20.act(db, sid)


def test_restart_revert_branch_and_divergence(make_session):
    sid = make_session()
    db = _db()
    nat20.start(db, sid, START)

    for _ in range(4):
        _step(db, sid)
    turn_save(session_id=sid, narration="Round one.", summary="r1")
    at_save = nat20.status(db, sid)

    for _ in range(8):
        _step(db, sid)
    turn_save(session_id=sid, narration="Later.", summary="r3")
    later = nat20.status(db, sid)
    assert later["calls"] == 12 and later != at_save

    # Process restart: the cache is gone, the log rebuilds the same combat.
    nat20._CACHE.clear()
    assert nat20.status(db, sid) == later

    # Undo one turn: LoreKit restores session_meta; Nat20 rebuilds to match.
    turn_revert(session_id=sid, steps=1)
    db = _db()
    assert nat20.status(db, sid) == at_save

    # Branch: play on differently from the reverted point, then name-save it.
    events = _step(db, sid)["events"]
    assert events and nat20.status(db, sid)["calls"] == 5
    turn_save(session_id=sid, narration="Branch.", summary="b")
    manual_save(session_id=sid, name="branch")

    # Load the named save after more play and land back on the branch state.
    branch_state = nat20.status(db, sid)
    _step(db, sid)
    turn_save(session_id=sid, narration="More.", summary="m")
    save_load(session_id=sid, name="branch")
    db = _db()
    assert nat20.status(db, sid) == branch_state

    # Tampered log: replay detects divergence instead of continuing silently.
    rec = nat20._load(db, sid)
    rec["digests"][2] = "0" * 16
    db.execute(
        "UPDATE session_meta SET value = ? WHERE session_id = ? AND key = ?", (json.dumps(rec), sid, nat20.META_KEY)
    )
    db.commit()
    nat20._CACHE.clear()
    with pytest.raises(nat20.ReplayDivergence, match="after call 2"):
        nat20.status(db, sid)


def test_end_clears_log(make_session):
    sid = make_session()
    db = _db()
    nat20.start(db, sid, START)
    for _ in range(3):
        _step(db, sid)
    outcome = nat20.end(db, sid)["outcome"]
    assert "residual_hp" in outcome
    assert nat20._load(db, sid) is None


def _to_pc_turn(db, sid):
    while not nat20.status(db, sid)["current"].startswith("char:"):
        nat20.act(db, sid)


def test_nat20_rejection_is_logged_and_replays(make_session, monkeypatch):
    # Nat20 resolves most bad actions as events, so force a raised error for one intent type.
    real = nat20.submit_player_intent

    async def flaky(handle, actor, intent):
        if intent.intent_type == "help":
            raise RuntimeError("engine says no")
        return await real(handle, actor, intent)

    monkeypatch.setattr(nat20, "submit_player_intent", flaky)

    sid = make_session()
    db = _db()
    nat20.start(db, sid, START)
    _to_pc_turn(db, sid)
    before = nat20.status(db, sid)["calls"]

    bad = {"intent_type": "help", "target_id": "mon:goblin0"}
    res = nat20.act(db, sid, "char:valeros", bad)
    assert res["error"], "engine error should be returned"
    assert nat20.status(db, sid)["calls"] == before + 1
    assert nat20._load(db, sid)["log"][-1]["error"] == res["error"]

    expected = nat20.status(db, sid)
    nat20._CACHE.clear()
    assert nat20.status(db, sid) == expected


def test_invalid_calls_are_not_logged(make_session):
    sid = make_session()
    db = _db()
    nat20.start(db, sid, START)
    cur = nat20.status(db, sid)["current"]
    assert not cur.startswith("char:"), "goblins act first in this fixture"
    for actor, intent in [
        ("char:valeros", {"intent_type": "dodge"}),  # out of turn
        ("char:nobody", {"intent_type": "dodge"}),  # unknown actor
        ("char:valeros", {"intent_type": "not-a-thing"}),  # bad shape
        (None, {"intent_type": "dodge"}),  # no actor
    ]:
        with pytest.raises(ValueError):
            nat20.act(db, sid, actor, intent)
    assert nat20.status(db, sid)["calls"] == 0


def test_monster_turn_refused_on_pc_turn(make_session):
    sid = make_session()
    db = _db()
    nat20.start(db, sid, START)
    _to_pc_turn(db, sid)
    calls = nat20.status(db, sid)["calls"]
    with pytest.raises(ValueError, match="submit an intent"):
        nat20.act(db, sid)
    assert nat20.status(db, sid)["calls"] == calls


def test_engine_version_mismatch_refuses_replay(make_session, monkeypatch):
    sid = make_session()
    db = _db()
    nat20.start(db, sid, START)
    _step(db, sid)
    nat20._CACHE.clear()
    monkeypatch.setattr(nat20, "ENGINE_VERSION", "0.0.0")
    with pytest.raises(nat20.ReplayDivergence, match="0.0.0"):
        nat20.status(db, sid)
    assert nat20._load(db, sid) is not None


def test_second_start_refused_until_end(make_session):
    sid = make_session()
    db = _db()
    nat20.start(db, sid, START)
    with pytest.raises(ValueError, match="already active"):
        nat20.start(db, sid, START)
    nat20.end(db, sid)
    nat20.start(db, sid, START)


def test_rebuild_of_a_long_combat_is_fast(make_session):
    sid = make_session()
    db = _db()
    nat20.start(db, sid, START)
    for _ in range(30):
        if nat20.status(db, sid)["ended"]:
            break
        _step(db, sid)
    calls = nat20.status(db, sid)["calls"]
    assert calls >= 20
    nat20._CACHE.clear()
    t = time.perf_counter()
    nat20.status(db, sid)
    assert time.perf_counter() - t < 1.0


def test_active_sessions_lists_combats(make_session):
    sid = make_session()
    db = _db()
    assert all(s != sid for s, _, _ in nat20.active_sessions(db))
    nat20.start(db, sid, START)
    _step(db, sid)
    assert (sid, nat20.ENGINE_VERSION, 1) in nat20.active_sessions(db)
