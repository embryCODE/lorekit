"""Persistent response effects and queue consequences from exact die inputs."""

import random

import pytest

from lorekit.alien import effects, scenes
from lorekit.alien.characters import build
from lorekit.alien.state import initial
from lorekit.db import LoreKitError


class Die:
    def __init__(self, value):
        self.value = value

    def randint(self, low, high):
        return self.value


def state():
    s = initial()
    for aid in ("a", "b"):
        s["actors"][aid] = build(
            {"name": aid, "attributes": dict(strength=3, agility=3, wits=3, empathy=3)},
            {"collections": {}},
            imported=True,
        )
    scenes.setup(s, {"zones": {"room": {"cluttered": True}}, "placements": {"a": "room", "b": "room"}})
    return s


def test_deflated_removes_jumpy_and_suppresses_future_jumpy():
    s = state()
    a = s["actors"]["a"]
    a["stress"] = 3
    effects.stress_response(a, Die(1))
    assert a["stress_responses"] == ["jumpy"]
    effects.stress_response(a, Die(6))
    assert a["stress_responses"] == ["deflated"]
    effects.stress_response(a, Die(1))
    assert a["stress_responses"] == ["deflated"]


def test_scream_queues_friendly_panic_and_catatonia_blocks_interrupts():
    s = state()
    a = s["actors"]["a"]
    a["stress"] = 9
    result = effects.panic(s, "a", Die(3))
    assert result["result"] == 9
    assert s["queue"] == [{"type": "panic", "actor": "b", "reason": "witnessed_scream"}]
    assert a["skip_turn"]
    s["queue"] = []
    a["panic_responses"] = [12]
    a.pop("skip_turn")
    scenes.start_combat(s, {"actors": ["a", "b"]}, random.Random(1))
    with pytest.raises(LoreKitError):
        scenes.spend(s, "a", "quick", out_of_turn=True)


def test_stronger_incompatible_panic_takes_precedence():
    s = state()
    a = s["actors"]["a"]
    a.update(stress=10, panic_responses=[12])
    result = effects.panic(s, "a", Die(3))
    assert result["suppressed_by"] == 12
    assert a["panic_responses"] == [12]


@pytest.mark.parametrize(
    "die,name", [(1, "jumpy"), (2, "tunnel_vision"), (3, "aggravated"), (4, "shakes"), (5, "frantic"), (6, "deflated")]
)
def test_each_stress_response_is_persisted(die, name):
    s = state()
    a = s["actors"]["a"]
    a["stress"] = 3
    result = effects.stress_response(a, Die(die))
    assert result["response"] == name
    assert name in a["stress_responses"]


def test_mess_up_forces_failure_and_queue_limit_preserves_work():
    s = state()
    a = s["actors"]["a"]
    a["stress"] = 4
    assert effects.stress_response(a, Die(6))["forced_failure"]
    assert a["stress"] == 5
    a["stress"] = 0
    for _ in range(150):
        effects.enqueue_panic(s, "a", "test event")
    result = effects.drain(s, Die(1))
    assert len(s["queue"]) == 50
    assert result[-1]["type"] == "queue_suspended"
    effects.drain(s, Die(1))
    assert not s["queue"]


@pytest.mark.parametrize("value", range(13))
def test_every_panic_result_has_a_persistent_disposition(value):
    s = state()
    a = s["actors"]["a"]
    a["resolve"] = 1 if value == 0 else 0
    a["stress"] = max(0, value - 1)
    out = effects.panic(s, "a", Die(1))
    assert out["result"] == value
    if value in (2, 3, 4, 10, 11):
        assert s["pending"]["type"] == "panic_choice" and s["pending"]["result"] == value
    elif value >= 5:
        assert value in a["panic_responses"]
    if value in (7, 8, 9):
        assert a["skip_turn"] and a["interrupt_blocked"]
    if value == 8:
        assert a["cover"] == "full"
    if value in (9, 11):
        assert s["queue"][0]["actor"] == "b"


def test_flee_cannot_pass_an_immobilizing_tail_or_blocked_exit():
    from lorekit.alien import decisions

    s = state()
    a = s["actors"]["a"]
    a["panic_responses"] = [10]
    a["immobilized_by"] = "b"
    s["pending"] = {"type": "panic_choice", "actor": "a", "result": 10}
    decisions.panic(s, {"actor": "a"}, {"collections": {}}, random.Random(1))
    assert a["panic_responses"] == [12]


def test_flee_zero_g_check_precedes_free_movement_and_relief():
    from lorekit.alien import checks, decisions
    from lorekit.alien.engine import complete_check

    s = state()
    scenes.setup(
        s,
        {
            "zones": {"room": {}, "void": {"zero_g": True}},
            "borders": [{"a": "room", "b": "void", "open": True}],
            "placements": {"a": "room", "b": "room"},
        },
    )
    a = s["actors"]["a"]
    a.update(stress=2, panic_responses=[10])
    s["pending"] = {"type": "panic_choice", "actor": "a", "result": 10}
    b = {"collections": {}}
    decisions.panic(s, {"actor": "a", "zone": "void", "away_from_threat": True}, b, Die(6))
    assert a["zone"] == "room" and a["stress"] == 2
    p = s["pending"]
    assert p["continuation"]["kind"] == "forced_move"
    out = checks.decide(s, {"actor": "a", "decision_id": p["id"], "choice": "accept"}, b, Die(6))
    complete_check(s, out, b, Die(6))
    assert a["zone"] == "void" and a["stress"] == 1
    assert s["actors"]["b"]["stress"] == 1


def test_frenzy_starts_combat_and_uses_an_ordinary_approach():
    from lorekit.alien import decisions

    s = state()
    s["actors"]["a"]["panic_responses"] = [11]
    s["pending"] = {"type": "panic_choice", "actor": "a", "result": 11}
    out = decisions.panic(s, {"actor": "a", "target": "b", "approach": True}, {"collections": {}}, random.Random(1))
    assert out["type"] == "defense_required"
    assert s["combat"]["budgets"]["a"]["quick"] == 1
    assert s["actors"]["a"]["frenzy_target"] == "b"
    assert scenes.range_band(s, "a", "b") == 0


def test_first_sight_is_deduplicated_and_revelation_stresses_observers():
    from lorekit.alien import characters, creatures

    s = state()
    b = {"collections": {"creatures": {"test": {"health": 2, "speed": 1, "skills": {}}}}}
    s["actors"]["xeno"] = creatures.build("test", "Specimen", b)
    s["actors"]["cover"] = characters.build(
        {
            "name": "Cover",
            "species": "synthetic",
            "secret_identity": True,
            "attributes": dict(strength=3, agility=3, wits=3, empathy=3),
        },
        b,
        imported=True,
    )
    scenes.setup(s, {"zones": {"room": {}}, "placements": {aid: "room" for aid in s["actors"]}})
    effects.horror(s, {"actor": "a", "source": "xeno", "trigger": "first_sight"})
    effects.horror(s, {"actor": "a", "source": "xeno", "trigger": "first_sight"})
    assert len(s["queue"]) == 1
    with pytest.raises(LoreKitError, match="Adjacent"):
        effects.horror(s, {"actor": "a", "source": "xeno", "trigger": "adjacent", "terrifying": True})
    effects.horror(s, {"actor": "a", "source": "cover", "trigger": "synthetic_reveal"})
    assert not s["actors"]["cover"]["secret_identity"]
    assert s["actors"]["a"]["stress"] == s["actors"]["b"]["stress"] == 1
    assert s["actors"]["cover"]["stress"] == 0
