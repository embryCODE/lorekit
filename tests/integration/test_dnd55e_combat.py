"""5e combat tools end to end: character rows -> Nat20 -> character rows."""

import json

import pytest

pytest.importorskip("dnd5e_engine")

from lorekit.backends import nat20  # noqa: E402
from lorekit.db import get_db  # noqa: E402
from lorekit.dnd55e import character as chars  # noqa: E402
from lorekit.tools.character import character_create  # noqa: E402
from lorekit.tools.dnd import (  # noqa: E402
    dnd_character_create,
    dnd_combat_act,
    dnd_combat_end,
    dnd_combat_start,
    dnd_combat_status,
)
from lorekit.tools.encounter import encounter_start  # noqa: E402

FIGHTER = {
    "species_slug": "human",
    "class_slug": "fighter",
    "level": 3,
    "ability_scores": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
}
WIZARD = {
    "species_slug": "elf-high",
    "class_slug": "wizard",
    "level": 3,
    "ability_scores": {"str": 8, "dex": 14, "con": 13, "int": 16, "wis": 12, "cha": 10},
}


def _party(sid):
    ids = {}
    for name, build in (("Valeros", FIGHTER), ("Ezren", WIZARD)):
        out = json.loads(dnd_character_create(session_id=sid, name=name, build=json.dumps(build)))
        ids[name] = out["character_id"]
    return ids


def _start(sid, ids, monsters=None, seed=11):
    monsters = monsters or [
        {"slug": "goblin-warrior", "cell": "1,0"},
        {"slug": "goblin-warrior", "cell": "1,2"},
    ]
    return dnd_combat_start(
        session_id=sid,
        monsters=json.dumps(monsters),
        grid="8x8",
        cells=json.dumps({"Valeros": "0,0", "Ezren": "0,2"}),
        seed=seed,
    )


def _attack(sid, st):
    actor = st["current"]
    foes = [e for e in st["order"] if e.startswith("mon:") and e not in st["dead"] and st["hp"].get(e, 0) > 0]
    weapon = "longsword" if actor == f"char:{st['ids']['Valeros']}" else "quarterstaff"
    out = dnd_combat_act(
        session_id=sid,
        actor=actor,
        intent=json.dumps({"intent_type": "attack", "target_id": foes[0], "weapon_id": weapon}),
    )
    assert not out.startswith("ERROR"), out
    return json.loads(out)


def test_full_combat_writes_back_to_character_rows(make_session):
    sid = make_session(system="dnd55e")
    ids = _party(sid)
    st = json.loads(_start(sid, ids))
    st["ids"] = ids
    assert st["current"] and set(st["party"]) == {f"char:{i}" for i in ids.values()}
    assert any("Goblin" in n for n in st["names"].values())

    for _ in range(40):
        if st["ended"]:
            break
        if st["current"].startswith("mon:"):
            st = json.loads(dnd_combat_act(session_id=sid))
        else:
            st = _attack(sid, st)
        st["ids"] = ids
        if not [e for e in st["order"] if e.startswith("mon:") and e not in st["dead"] and st["hp"].get(e, 0) > 0]:
            break

    live_hp = {e: st["hp"][e] for e in st["party"]}
    summary = json.loads(dnd_combat_end(session_id=sid))
    assert summary["ended_reason"] and set(summary["party"]) == {"Valeros", "Ezren"}

    db = get_db()
    for name, cid in ids.items():
        row = chars.read(db, cid)
        assert row["hp_current"] == max(0, live_hp[f"char:{cid}"]), name
        assert row["hp_current"] <= row["hp_max"]
    assert nat20._load(db, sid) is None


def test_slots_spent_in_combat_persist(make_session):
    sid = make_session(system="dnd55e")
    ids = _party(sid)
    st = json.loads(_start(sid, ids, monsters=[{"slug": "goblin-warrior", "cell": "3,2"}]))
    wiz = f"char:{ids['Ezren']}"
    for _ in range(30):
        if st["current"] == wiz:
            break
        st = json.loads(dnd_combat_act(session_id=sid)) if st["current"].startswith("mon:") else _pass(sid, st)
    cast = dnd_combat_act(
        session_id=sid,
        actor=wiz,
        intent=json.dumps(
            {
                "intent_type": "cast_spell",
                "spell_id": "magic-missile",
                "target_id": "mon:goblin-warrior-1",
                "slot_level": 1,
            }
        ),
    )
    assert not cast.startswith("ERROR"), cast
    left = json.loads(dnd_combat_status(session_id=sid))["slots"][wiz]
    assert left["1"] == 3
    dnd_combat_end(session_id=sid)
    assert chars.read(get_db(), ids["Ezren"])["slots"]["1"] == 3


def _pass(sid, st):
    return json.loads(dnd_combat_act(session_id=sid, actor=st["current"], intent=json.dumps({"intent_type": "pass"})))


def test_unknown_monster_lists_matches_and_writes_nothing(make_session):
    sid = make_session(system="dnd55e")
    ids = _party(sid)
    out = _start(sid, ids, monsters=[{"slug": "goblin-warriorr", "cell": "3,3"}])
    assert out.startswith("ERROR") and "goblin" in out
    assert nat20._load(get_db(), sid) is None


def test_start_refused_when_a_pc_is_at_zero_hp(make_session):
    sid = make_session(system="dnd55e")
    ids = _party(sid)
    db = get_db()
    chars.write(db, ids["Valeros"], hp_current=0)
    db.commit()
    assert "0 HP" in _start(sid, ids)


def test_out_of_turn_is_an_error_and_cruncher_tools_are_refused(make_session):
    sid = make_session(system="dnd55e")
    ids = _party(sid)
    st = json.loads(_start(sid, ids))
    other = next(p for p in st["party"] if p != st["current"])
    out = dnd_combat_act(session_id=sid, actor=other, intent=json.dumps({"intent_type": "dodge"}))
    assert out.startswith("ERROR")
    assert encounter_start(session_id=sid).startswith("ERROR")
    assert character_create  # generic NPC creation stays available on 5e sessions


def test_combat_tools_refused_on_cruncher_session(make_session):
    assert dnd_combat_status(session_id=make_session(system="pf2e")).startswith("ERROR")


def test_shield_armed_on_own_turn_fires_when_hit(make_session):
    sid = make_session(system="dnd55e")
    cid = json.loads(dnd_character_create(session_id=sid, name="Ezren", build=json.dumps(WIZARD)))["character_id"]
    st = json.loads(
        dnd_combat_start(
            session_id=sid,
            monsters=json.dumps([{"slug": "goblin-warrior", "cell": "1,0"}]),
            grid="6x6",
            cells=json.dumps({"Ezren": "0,0"}),
            seed=5,
        )
    )
    me = f"char:{cid}"
    while st["current"] != me:
        st = json.loads(dnd_combat_act(session_id=sid))
    arm = {"intent_type": "ready", "reaction_trigger": "hit_by_attack", "spell_id": "shield", "slot_level": 1}
    out = json.loads(dnd_combat_act(session_id=sid, actor=me, intent=json.dumps(arm)))
    seen = [e.get("type") for step in out["steps"] for e in step["events"]]
    assert "reaction_triggered" in seen and seen.index("reaction_triggered") < seen.index("attack_rolled")
    assert out["slots"][me]["1"] == 3


def test_agent_driven_npc_pauses_auto_turns_and_accepts_intents(make_session):
    sid = make_session(system="dnd55e")
    ids = _party(sid)
    monsters = [
        {"slug": "goblin-warrior", "cell": "4,0", "agent": True, "name": "Grisk"},
        {"slug": "goblin-warrior", "cell": "4,2"},
    ]
    st = json.loads(_start(sid, ids, monsters=monsters))
    for _ in range(12):
        if st.get("awaiting_agent") or st["ended"]:
            break
        st = _pass(sid, st) if st["current"].startswith("char:") else json.loads(dnd_combat_act(session_id=sid))
    assert st["awaiting_agent"] == "mon:goblin-warrior-1"
    assert st["current_name"] == "Grisk"

    # the NPC's agent decides to move; the intent is validated and logged like a PC's
    out = json.loads(
        dnd_combat_act(
            session_id=sid,
            actor="mon:goblin-warrior-1",
            intent=json.dumps({"intent_type": "move", "target_zone_id": "3,0"}),
        )
    )
    assert out["zones"]["mon:goblin-warrior-1"] == "3,0"
    # a non-agent monster cannot be given intents
    bad = dnd_combat_act(session_id=sid, actor="mon:goblin-warrior-2", intent=json.dumps({"intent_type": "dodge"}))
    assert bad.startswith("ERROR")


def test_monsters_get_a_stat_block_attack_bonus(make_session):
    sid = make_session(system="dnd55e")
    ids = _party(sid)
    _start(sid, ids, monsters=[{"slug": "goblin-warrior", "cell": "1,0"}])
    gob = nat20._load(get_db(), sid)["start"]["encounter"][0]
    assert gob["attack_bonus"] == 4  # Dex +2, proficiency +2
