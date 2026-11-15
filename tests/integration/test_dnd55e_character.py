"""5e characters: built through Nat20, state owned by LoreKit rows."""

import json

import pytest

pytest.importorskip("dnd5e_engine")

from lorekit.backends import nat20  # noqa: E402
from lorekit.db import get_db  # noqa: E402
from lorekit.dnd55e import character as dnd  # noqa: E402
from lorekit.tools.character import character_view  # noqa: E402
from lorekit.tools.dnd import dnd_character_create, dnd_character_level  # noqa: E402

AS = {"str": 8, "dex": 14, "con": 13, "int": 16, "wis": 12, "cha": 10}
WIZARD = {"species_slug": "elf-high", "class_slug": "wizard", "level": 3, "ability_scores": AS}


def _create(sid, name="Ezren", build=None):
    out = dnd_character_create(session_id=sid, name=name, build=json.dumps(build or WIZARD))
    return out, (json.loads(out) if not out.startswith("ERROR") else None)


def test_create_stores_build_and_derived_sheet(make_session):
    sid = make_session(system="dnd55e")
    _, c = _create(sid)
    assert c["build_spec"] == WIZARD
    assert c["hp_current"] == c["hp_max"] > 0
    assert c["slots"] == {"1": 4, "2": 2} and c["slots"] == c["slots_max"]
    assert c["hit_dice"] == {"6": 3} and c["exhaustion"] == 0 and c["conditions"] == []
    assert c["ac"] >= 10 and c["speed"] == 30


def test_invalid_build_writes_no_row(make_session):
    sid = make_session(system="dnd55e")
    db = get_db()
    before = db.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
    out, _ = _create(sid, build={**WIZARD, "class_slug": "not-a-class"})
    assert out.startswith("ERROR") and "invalid 5e build" in out
    assert db.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == before


def test_create_refused_on_cruncher_session(make_session):
    out, _ = _create(make_session(system="pf2e"))
    assert out.startswith("ERROR")


def test_level_up_keeps_state_within_new_maximums(make_session):
    sid = make_session(system="dnd55e")
    _, c = _create(sid)
    cid = c["character_id"]
    db = get_db()
    dnd.write(db, cid, hp_current=5, slots={"1": 1, "2": 0})  # wounded and spent
    db.commit()

    out = json.loads(dnd_character_level(character_id=cid, changes=json.dumps({"level": 4})))
    assert out["build_spec"]["level"] == 4
    assert out["hp_max"] > c["hp_max"]
    assert out["hp_current"] == 5 + (out["hp_max"] - c["hp_max"])  # gained HP added
    assert out["slots_max"]["2"] == 3 and out["slots"]["2"] == 1  # new level-2 slot arrives full
    assert out["slots"]["1"] == 1  # unchanged slots stay spent
    assert out["hit_dice"] == {"6": 4}
    assert db.execute("SELECT level FROM characters WHERE id = ?", (cid,)).fetchone()[0] == 4
    assert all(out["slots"][k] <= out["slots_max"][k] for k in out["slots"])


def test_invalid_level_change_leaves_character_untouched(make_session):
    sid = make_session(system="dnd55e")
    _, c = _create(sid)
    bad = dnd_character_level(character_id=c["character_id"], changes=json.dumps({"class_slug": "nope"}))
    assert bad.startswith("ERROR")
    assert dnd.read(get_db(), c["character_id"])["build_spec"] == WIZARD


def test_character_view_notes_active_combat(make_session):
    sid = make_session(system="dnd55e")
    _, c = _create(sid, name="Valeros", build={**WIZARD, "class_slug": "fighter", "species_slug": "human"})
    assert "dnd_combat_status" not in character_view(character_id=c["character_id"])
    db = get_db()
    nat20.start(
        db,
        sid,
        {
            "seed": 1,
            "grid": [6, 6],
            "party": [
                {
                    "build": {**WIZARD, "class_slug": "fighter", "species_slug": "human"},
                    "instance": {
                        "entity_id": f"char:{c['character_id']}",
                        "name": "Valeros",
                        "hp_current": c["hp_current"],
                        "hp_max": c["hp_max"],
                        "ac": c["ac"],
                        "initiative": 1,
                        "zone_id": [0, 0],
                    },
                }
            ],
            "encounter": [{"entity_id": "mon:g", "slug": "goblin-warrior", "hp": 7, "init": 10, "zone_id": [3, 3]}],
        },
    )
    assert "dnd_combat_status" in character_view(character_id=c["character_id"])


def test_bad_choice_error_lists_valid_options_and_good_tokens_work(make_session):
    sid = make_session(system="dnd55e")
    fighter = {
        "species_slug": "halfling",
        "class_slug": "fighter",
        "level": 3,
        "background_slug": "criminal",
        "ability_scores": {"str": 15, "dex": 13, "con": 14, "int": 8, "wis": 12, "cha": 10},
        "equipment": ["studded-leather-armor", "warhammer"],
    }
    bad = dnd_character_create(
        session_id=sid, name="Roxy", build=json.dumps({**fighter, "selected_choices": ["dueling"]})
    )
    assert bad.startswith("ERROR") and "defense" in bad and "skill:<skill>" in bad
    good = {**fighter, "selected_choices": ["defense", "skill:acrobatics", "skill:persuasion"]}
    out = json.loads(dnd_character_create(session_id=sid, name="Roxy", build=json.dumps(good)))
    assert out["character_id"]
