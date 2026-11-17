"""Scene and action accounting boundaries independent of proprietary catalogs."""

import random

import pytest

from lorekit.alien import scenes
from lorekit.alien.characters import build
from lorekit.alien.state import initial
from lorekit.db import LoreKitError


def state():
    s = initial()
    s["actors"] = {
        str(i): build(
            {"name": str(i), "attributes": dict(strength=3, agility=3, wits=4, empathy=4)},
            {"collections": {}},
            imported=True,
        )
        for i in range(3)
    }
    scenes.setup(
        s,
        {
            "zones": {"room": {"cluttered": True}, "hall": {}},
            "borders": [{"a": "room", "b": "hall", "door": True}],
            "placements": {aid: "room" for aid in s["actors"]},
        },
    )
    return s


def test_hold_off_does_not_refund_an_interrupt():
    s = state()
    scenes.start_combat(s, {"actors": list(s["actors"])}, random.Random(1))
    slots = s["combat"]["slots"]
    first, last = slots[0]["actors"][0], slots[-1]["actors"][0]
    scenes.spend(s, first, "quick", out_of_turn=True)
    scenes.hold_off(s, first, slots[-1]["card"])
    assert s["combat"]["budgets"][first]["quick"] == 1
    assert slots[0]["actors"] == [last]
    with pytest.raises(LoreKitError, match="turn"):
        scenes.spend(s, first, "full")


def test_interrupts_exhaust_full_action():
    s = state()
    scenes.start_combat(s, {"actors": list(s["actors"])}, random.Random(1))
    aid = s["combat"]["slots"][0]["actors"][0]
    scenes.spend(s, aid, "quick", out_of_turn=True)
    scenes.spend(s, aid, "quick", out_of_turn=True)
    with pytest.raises(LoreKitError, match="unavailable"):
        scenes.spend(s, aid, "full")


def test_sealed_border_rejects_movement_and_range_differentiates_adjacency():
    s = state()
    assert scenes.range_band(s, "0", "1") == 1
    s["actors"]["0"]["adjacent"] = ["1"]
    assert scenes.range_band(s, "0", "1") == 0
    s["scene"]["borders"][0]["sealed"] = True
    with pytest.raises(LoreKitError, match="traversable"):
        scenes.move(s, "0", "hall", {"collections": {}})


def test_initiative_overflow_does_not_create_duplicate_cards():
    import copy

    s = state()
    for i in range(3, 11):
        s["actors"][str(i)] = copy.deepcopy(s["actors"]["0"])
    with pytest.raises(LoreKitError, match="ten initiative"):
        scenes.start_combat(s, {"actors": list(s["actors"])}, random.Random(1))


def test_fractional_load_counts_reloads_excludes_drops_and_stresses_each_move():
    from lorekit.alien.characters import load_weight

    s = state()
    b = {"collections": {"items": {"sample": {"weight": 0.5, "capacity": 1}}}}
    a = s["actors"]["0"]
    a["inventory"] = [{"id": "load", "content_id": "sample", "quantity": 12, "reloads": 1}]
    assert load_weight(a, b) == (6.25, 6)
    scenes.move(s, "0", "hall", b)
    scenes.move(s, "0", "room", b)
    assert a["stress"] == 2
    a["inventory"][0]["dropped"] = True
    assert load_weight(a, b) == (0, 6)
    a["inventory"][0].update(dropped=False, quantity=25)
    with pytest.raises(LoreKitError, match="encumbrance"):
        scenes.move(s, "0", "hall", b)
    a["inventory"][0]["quantity"] = 12
    a["species"] = "synthetic"
    scenes.move(s, "0", "hall", b)
    assert a["stress"] == 2


def test_crawlspace_order_blocks_friendlies_and_tracks_entry_end():
    s = state()
    scenes.setup(
        s,
        {
            "zones": {"duct": {"crawlspace": True, "capacity": 3}, "hall": {"outer_armor": 3, "auto_seal": True}},
            "borders": [{"a": "duct", "b": "hall", "open": True, "ends": {"duct": "head"}}],
            "placements": {"0": "duct", "1": "duct", "2": "hall"},
            "crawl_order": {"duct": ["0", "1"]},
        },
    )
    with pytest.raises(LoreKitError, match="past another"):
        scenes.move(s, "1", "hall", {"collections": {}})
    scenes.move(s, "0", "hall", {"collections": {}})
    scenes.move(s, "2", "duct", {"collections": {}})
    assert s["scene"]["crawl_order"]["duct"] == ["2", "1"]


def test_passage_permission_is_one_crossing_and_broken_blockers_do_not_count():
    s = state()
    edge = s["scene"]["borders"][0]
    edge["blockers"] = ["1"]
    with pytest.raises(LoreKitError, match="blocks this"):
        scenes.move(s, "0", "hall", {"collections": {}})
    s["actors"]["0"]["passed"] = ["1"]
    scenes.move(s, "0", "hall", {"collections": {}})
    assert "passed" not in s["actors"]["0"]
    s["actors"]["1"]["health"] = 0
    scenes.move(s, "0", "room", {"collections": {}})


def test_hold_off_defers_stun_recovery_until_turn_is_used():
    from lorekit.alien.engine import transition

    s = state()
    for a in s["actors"].values():
        a["kind"] = "npc"
    scenes.start_combat(s, {"actors": list(s["actors"])}, random.Random(1))
    combat = s["combat"]
    first = combat["slots"][0]["actors"][0]
    last = combat["slots"][-1]["actors"][0]
    s["actors"][first]["stunned"] = True
    assert not s["queue"]
    transition(s, {"op": "hold_off", "actor": first, "card": combat["slots"][-1]["card"]}, random.Random(1))
    assert not s["queue"] and s["actors"][first]["stunned"]
    transition(s, {"op": "end_turn", "actor": last}, random.Random(1))
    middle = combat["slots"][combat["index"]]["actors"][0]
    transition(s, {"op": "end_turn", "actor": middle}, random.Random(1))
    result = transition(s, {"op": "begin_turn", "actor": first}, random.Random(5))
    assert result["type"] == "turn_effects"
    assert result["events"][0]["type"] == "stun_result"
    assert not s["actors"][first]["stunned"]


def test_full_action_help_must_be_prepared_on_helpers_turn():
    from lorekit.alien import actions, checks

    s = state()
    for a in s["actors"].values():
        a["kind"] = "npc"
    scenes.start_combat(s, {"actors": ["0", "1"]}, random.Random(1))
    first = s["combat"]["slots"][0]["actors"][0]
    helper = "1" if first == "0" else "0"
    b = {"collections": {}}
    with pytest.raises(LoreKitError, match="full action cannot interrupt"):
        checks.start(s, {"actor": first, "skill": "comtech", "helpers": [helper]}, b, random.Random(1))
    scenes.hold_off(s, first, s["combat"]["slots"][-1]["card"])
    s["actors"][helper].update(species="synthetic", health=0)
    actions.act(s, {"actor": helper, "action": "help", "target": first, "skill": "comtech"}, b, random.Random(1))
    scenes.end_turn(s, helper, random.Random(1), b)
    result = checks.start(s, {"actor": first, "skill": "comtech", "helpers": [helper]}, b, random.Random(1))
    assert result["type"] == "check_complete"
    assert s["combat"]["budgets"][helper]["full"]
    assert "help_offer" not in s["actors"][helper]


def test_overkill_requires_attack_against_operating_zero_health_synthetic():
    s = state()
    s["actors"]["1"].update(species="synthetic", health=0, side="opposition")
    scenes.start_combat(s, {"actors": ["0", "1"], "surprise": ["0"]}, random.Random(1))
    s["actors"]["0"]["overkill"] = ["1"]
    with pytest.raises(LoreKitError, match="Overkill requires attacking"):
        scenes.end_turn(s, "0", random.Random(1), {"collections": {}})


def test_reinforcements_join_next_round_without_refunding_existing_actions():
    import copy

    s = state()
    scenes.start_combat(s, {"actors": ["0", "1"]}, random.Random(1))
    scenes.spend(s, "0", "quick", out_of_turn=True)
    before = copy.deepcopy(s["combat"])
    result = scenes.enter_combat(s, {"actors": ["0", "2"]}, {"collections": {}}, random.Random(2))
    assert result["initiative"] == "next_round" and result["actors"] == ["2"]
    assert s["combat"]["slots"] == before["slots"]
    assert s["combat"]["budgets"]["0"] == before["budgets"]["0"]
    scenes.draw_round(s, random.Random(2))
    assert any("2" in slot["actors"] for slot in s["combat"]["slots"])


def test_operating_zero_health_synthetic_can_block_a_passage():
    from lorekit.alien import actions

    s = state()
    s["actors"]["0"].update(species="synthetic", health=0)
    actions.act(s, {"actor": "0", "action": "block", "zone": "hall"}, {"collections": {}}, random.Random(1))
    with pytest.raises(LoreKitError, match="blocks this passage"):
        scenes.move(s, "1", "hall", {"collections": {}})
