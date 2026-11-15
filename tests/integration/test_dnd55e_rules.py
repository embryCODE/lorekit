"""Out-of-combat 5e checks and rests, resolved by Nat20 and written to character rows."""

import json

import pytest

pytest.importorskip("dnd5e_engine")

from lorekit.db import get_db  # noqa: E402
from lorekit.dnd55e import character as chars  # noqa: E402
from lorekit.tools.dnd import dnd_character_create, dnd_check, dnd_rest  # noqa: E402

WIZARD = {
    "species_slug": "elf-high",
    "class_slug": "wizard",
    "level": 3,
    "ability_scores": {"str": 8, "dex": 14, "con": 13, "int": 16, "wis": 12, "cha": 10},
}


def _wiz(make_session):
    sid = make_session(system="dnd55e")
    cid = json.loads(dnd_character_create(session_id=sid, name="Ezren", build=json.dumps(WIZARD)))["character_id"]
    return sid, cid


def test_check_is_reproducible_from_its_seed(make_session):
    _, cid = _wiz(make_session)
    a = json.loads(dnd_check(character_id=cid, kind="skill", name="stealth", dc=13, seed=42))
    b = json.loads(dnd_check(character_id=cid, kind="skill", name="stealth", dc=13, seed=42))
    assert a == b and a["total"] == a["natural_roll"] + a["modifier"] and a["success"] == (a["total"] >= 13)


def test_proficiency_applies_to_proficient_saves_only(make_session):
    _, cid = _wiz(make_session)
    int_save = json.loads(dnd_check(character_id=cid, kind="saving_throw", name="int", dc=10, seed=1))
    str_save = json.loads(dnd_check(character_id=cid, kind="saving_throw", name="strength", dc=10, seed=1))
    assert int_save["modifier"] == 3 + 2 and int_save["proficient"]  # +3 Int, +2 proficiency
    assert str_save["modifier"] == -1 and not str_save["proficient"]


def test_advantage_and_disadvantage_cancel(make_session):
    _, cid = _wiz(make_session)
    both = json.loads(
        dnd_check(character_id=cid, kind="ability", name="dexterity", dc=10, advantage=True, disadvantage=True, seed=9)
    )
    assert not both["advantage"] and not both["disadvantage"]


def test_bad_inputs_are_errors(make_session):
    _, cid = _wiz(make_session)
    assert dnd_check(character_id=cid, kind="ability", name="luck", dc=10).startswith("ERROR")
    assert dnd_check(character_id=cid, kind="dance", name="x", dc=10).startswith("ERROR")


def test_check_is_journaled_with_its_seed(make_session):
    sid, cid = _wiz(make_session)
    dnd_check(character_id=cid, kind="skill", name="arcana", dc=12, seed=77)
    rows = (
        get_db().execute("SELECT content FROM journal WHERE session_id = ? AND entry_type = 'check'", (sid,)).fetchall()
    )
    assert rows and "seed 77" in rows[0][0]


def test_short_rest_spends_hit_dice_and_heals(make_session):
    _, cid = _wiz(make_session)
    db = get_db()
    chars.write(db, cid, hp_current=3)
    db.commit()
    out = json.loads(dnd_rest(character_id=cid, type="short", hit_dice=2, seed=5))
    assert out["dice_spent"] == 2 and len(out["rolls"]) == 2 and out["healed"] > 0
    row = chars.read(get_db(), cid)
    assert row["hit_dice"] == {"6": 1} and 3 < row["hp_current"] <= row["hp_max"]
    assert dnd_rest(character_id=cid, type="short", hit_dice=5).startswith("ERROR") is False  # spends what is left


def test_short_rest_with_no_dice_left_is_an_error(make_session):
    _, cid = _wiz(make_session)
    db = get_db()
    chars.write(db, cid, hit_dice={"6": 0})
    db.commit()
    assert dnd_rest(character_id=cid, type="short", hit_dice=1).startswith("ERROR")


def test_long_rest_restores_hp_slots_dice_and_reduces_exhaustion(make_session):
    _, cid = _wiz(make_session)
    db = get_db()
    chars.write(db, cid, hp_current=2, slots={"1": 0, "2": 0}, hit_dice={"6": 0}, exhaustion=2)
    db.commit()
    out = json.loads(dnd_rest(character_id=cid, type="long"))
    row = chars.read(get_db(), cid)
    assert row["hp_current"] == row["hp_max"] and row["slots"] == row["slots_max"]
    assert row["hit_dice"] == {"6": 1}  # half of 3, rounded down
    assert row["exhaustion"] == 1 and out["exhaustion"] == 1


def test_rules_tools_refused_on_cruncher_session(make_session):
    from lorekit.tools.character import character_create

    sid = make_session(system="pf2e")
    cid = int(character_create(session=sid, name="Seoni", level=1).split(": ")[1])
    assert dnd_rest(character_id=cid, type="long").startswith("ERROR")
    assert dnd_check(character_id=cid, kind="skill", name="stealth", dc=10).startswith("ERROR")
