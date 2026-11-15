"""A whole 5e session through the MCP tool functions, with a restart and a revert mid-fight."""

import json

import pytest

pytest.importorskip("dnd5e_engine")
pytest.importorskip("sqlite_vec")

from lorekit.backends import nat20  # noqa: E402
from lorekit.db import get_db  # noqa: E402
from lorekit.dnd55e import character as chars  # noqa: E402
from lorekit.tools.dnd import (  # noqa: E402
    dnd_character_create,
    dnd_check,
    dnd_combat_act,
    dnd_combat_end,
    dnd_combat_start,
    dnd_combat_status,
    dnd_rest,
)
from lorekit.tools.narrative import turn_revert, turn_save  # noqa: E402

FIGHTER = {
    "species_slug": "human",
    "class_slug": "fighter",
    "level": 3,
    "ability_scores": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
}


def _alive(st):
    return [e for e in st["order"] if e.startswith("mon:") and e not in st["dead"] and st["hp"].get(e, 0) > 0]


def _status(sid):
    return json.loads(dnd_combat_status(session_id=sid))


def test_session_from_character_creation_to_rest(make_session):
    sid = make_session(system="dnd55e")
    cid = json.loads(dnd_character_create(session_id=sid, name="Valeros", build=json.dumps(FIGHTER)))["character_id"]
    me = f"char:{cid}"

    check = json.loads(dnd_check(character_id=cid, kind="skill", name="athletics", dc=12, seed=3))
    assert check["total"] == check["natural_roll"] + check["modifier"]

    st = json.loads(
        dnd_combat_start(
            session_id=sid,
            monsters=json.dumps([{"slug": "goblin-warrior", "cell": "1,0"}, {"slug": "goblin-warrior", "cell": "1,2"}]),
            grid="8x8",
            cells=json.dumps({"Valeros": "0,0"}),
            seed=21,
        )
    )
    turn_save(session_id=sid, narration="The goblins spring the ambush.", summary="start")

    def swing(st):
        out = json.loads(
            dnd_combat_act(
                session_id=sid,
                actor=me,
                intent=json.dumps({"intent_type": "attack", "target_id": _alive(st)[0], "weapon_id": "longsword"}),
            )
        )
        return out

    # play a few rounds, saving a checkpoint after the second PC action
    steps = 0
    while st["current"] != me:
        st = json.loads(dnd_combat_act(session_id=sid))
    for _ in range(2):
        st = swing(st)
        steps += 1
    turn_save(session_id=sid, narration="Steel rings.", summary="two swings")
    saved = _status(sid)

    for _ in range(2):
        if st["ended"] or me not in st["party"] or not _alive(st):
            break
        if st["current"] == me:
            st = swing(st)
    turn_save(session_id=sid, narration="More fighting.", summary="later")

    # restart: drop the in-memory combat; the log rebuilds the same state
    later = _status(sid)
    nat20._CACHE.clear()
    assert _status(sid) == later

    # revert to the earlier checkpoint
    turn_revert(session_id=sid, steps=1)
    assert _status(sid) == saved

    # finish the fight
    for _ in range(80):
        st = _status(sid)
        if st["ended"] or not _alive(st) or st["hp"][me] <= 0:
            break
        if st["current"] == me:
            st = swing(st)
        else:
            st = json.loads(dnd_combat_act(session_id=sid))

    final_hp = _status(sid)["hp"][me]
    summary = json.loads(dnd_combat_end(session_id=sid))
    assert summary["ended_reason"] in ("victory", "defeat_tpk")

    row = chars.read(get_db(), cid)
    assert row["hp_current"] == max(0, final_hp)
    assert nat20._load(get_db(), sid) is None

    if row["hp_current"] > 0:
        rest = json.loads(dnd_rest(character_id=cid, type="short", hit_dice=1, seed=2))
        assert rest["dice_spent"] == 1
        assert chars.read(get_db(), cid)["hit_dice"] == {"10": 2}
        long = json.loads(dnd_rest(character_id=cid, type="long"))
        assert chars.read(get_db(), cid)["hp_current"] == chars.read(get_db(), cid)["hp_max"], long
