"""Hidden movement and passive-detection rules."""

import random

import pytest

from lorekit.alien import checks, scenes, stealth
from lorekit.alien.characters import build
from lorekit.alien.state import initial
from lorekit.db import LoreKitError


def setup():
    s = initial()
    b = {"collections": {}}
    for aid, kind in (("pc", "pc"), ("npc", "npc")):
        s["actors"][aid] = build(
            {"name": aid, "kind": kind, "attributes": dict(strength=3, agility=3, wits=4, empathy=4)}, b, imported=True
        )
    scenes.setup(
        s,
        {
            "zones": {"a": {"cluttered": True}, "b": {"cluttered": True}},
            "borders": [{"a": "a", "b": "b", "open": True}],
            "placements": {"pc": "a", "npc": "b"},
        },
    )
    return s, b


def test_detection_is_passive_and_pending_choice_cannot_be_stolen():
    s, b = setup()
    s["actors"]["pc"]["stress"] = 6
    result = stealth.detect(s, ["pc"], ["npc"], b, random.Random(1))
    assert result["type"] == "detection"
    assert all(not check["events"] for side in result["checks"] for check in side)
    p = s["pending"]
    loser = "npc" if "pc" in p["winner"] else "pc"
    with pytest.raises(LoreKitError, match="winning side"):
        stealth.decide(s, {"actor": loser, "decision_id": p["id"], "choice": "reveal"}, b, random.Random(1))


def test_hidden_actor_not_automatically_detected_by_zone_entry():
    s, b = setup()
    s["actors"]["npc"]["hidden"] = True
    result = stealth.act(s, {"actor": "pc", "action": "move", "zone": "b"}, b, random.Random(1))
    assert result["type"] == "stealth_action"
    assert s["pending"] is None
    assert s["actors"]["npc"]["hidden"]


def test_npc_cannot_move_before_pc_phase():
    s, b = setup()
    with pytest.raises(LoreKitError, match="phase"):
        stealth.act(s, {"actor": "npc", "action": "move", "zone": "a"}, b, random.Random(1))


def test_stealth_allegiance_is_independent_of_pc_npc_control():
    s, b = setup()
    s["actors"]["npc"]["side"] = "crew"
    out = stealth.act(s, {"actor": "pc", "action": "wait"}, b, random.Random(1))
    assert out["type"] == "stealth_action"
    assert s["pending"] is None and s["combat"] is None

    s, b = setup()
    s["actors"]["pc"]["kind"] = "npc"
    s["scene"]["phase"] = "npc"
    out = stealth.act(s, {"actor": "pc", "action": "wait"}, b, random.Random(1))
    assert out["type"] in ("detection", "combat_started")
    assert s["pending"] is not None or s["combat"] is not None


def test_dropped_tracker_cannot_be_used_remotely_or_spend_power():
    s, b = setup()
    b["collections"]["items"] = {"tracker": {"weight": 1, "tracker_range": 2, "capacity": 3, "supply_type": "power"}}
    a = s["actors"]["pc"]
    entry = {"id": "tracker", "content_id": "tracker", "quantity": 1, "supply": 3, "dropped": True, "ground_zone": "b"}
    a["inventory"].append(entry)
    with pytest.raises(LoreKitError, match="available motion tracker"):
        stealth.tracker(s, {"actor": "pc", "item": "tracker"}, b, random.Random(1))
    assert entry["supply"] == 3 and "tracker_stretch" not in a
