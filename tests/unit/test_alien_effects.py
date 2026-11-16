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
        s["actors"][aid] = build({"name": aid, "attributes": dict(strength=3, agility=3, wits=3, empathy=3)}, {"collections": {}}, imported=True)
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


@pytest.mark.parametrize("die,name", [(1,"jumpy"),(2,"tunnel_vision"),(3,"aggravated"),(4,"shakes"),(5,"frantic"),(6,"deflated")])
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
