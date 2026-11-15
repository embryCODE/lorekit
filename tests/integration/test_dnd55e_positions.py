"""Two creatures must never share a grid cell."""

import json

import pytest

pytest.importorskip("dnd5e_engine")

from dnd5e_engine import get_live  # noqa: E402

from lorekit.backends import nat20  # noqa: E402
from lorekit.db import get_db  # noqa: E402
from lorekit.tools.dnd import dnd_character_create, dnd_combat_act, dnd_combat_start  # noqa: E402

FIGHTER = {
    "species_slug": "human",
    "class_slug": "fighter",
    "level": 1,
    "ability_scores": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
}


def _positions(sid):
    handle, _ = nat20._live(get_db(), sid)
    return dict(get_live(handle).actor_zone)


def _assert_distinct(sid):
    zones = _positions(sid)
    assert len(set(zones.values())) == len(zones), zones


@pytest.mark.parametrize("seed", range(1, 9))
def test_monsters_never_stack_while_closing_in(make_session, seed):
    sid = make_session(system="dnd55e")
    dnd_character_create(session_id=sid, name="Roxy", build=json.dumps(FIGHTER))
    out = dnd_combat_start(
        session_id=sid,
        monsters=json.dumps(
            [
                {"slug": "goblin-warrior", "cell": "6,5"},
                {"slug": "goblin-warrior", "cell": "6,6"},
            ]
        ),
        grid="8x8",
        cells=json.dumps({"Roxy": "2,6"}),
        seed=seed,
    )
    st = json.loads(out)
    _assert_distinct(sid)
    for _ in range(6):
        if st["ended"] or not st["current"].startswith("mon:"):
            break
        st = json.loads(dnd_combat_act(session_id=sid))
        _assert_distinct(sid)


def test_start_rejects_shared_cell(make_session):
    sid = make_session(system="dnd55e")
    dnd_character_create(session_id=sid, name="Roxy", build=json.dumps(FIGHTER))
    out = dnd_combat_start(
        session_id=sid,
        monsters=json.dumps(
            [
                {"slug": "goblin-warrior", "cell": "6,5"},
                {"slug": "goblin-warrior", "cell": "6,5"},
            ]
        ),
        grid="8x8",
        cells=json.dumps({"Roxy": "2,6"}),
    )
    assert out.startswith("ERROR")
    assert "cell" in out
