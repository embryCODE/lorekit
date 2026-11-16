"""User-selected Beta 2 acid ruling: armor-absorbed hits can continue."""

from lorekit.alien import acid
from lorekit.alien.characters import build
from lorekit.alien.state import initial


class AllSixes:
    def randint(self, low, high):
        return 6


def test_absorbed_initial_hit_corrodes_and_continues_with_half_dice():
    bundle = {"collections": {}}
    state = initial()
    a = build(
        {"name": "Test", "attributes": dict(strength=3, agility=3, wits=3, empathy=3), "armor": 10},
        bundle,
        imported=True,
    )
    state["actors"]["target"] = a
    result = acid.initial(state, "target", 3, bundle, AllSixes())
    assert result["health_damage"] == 0
    assert result["armor_after"] == 7
    assert result["continues"]
    assert a["acid"] == [{"dice": 2, "due": 1}]
    assert a["health"] == 3
    state["clocks"]["round"] = 1
    next_result = acid.tick(state, "target", bundle, AllSixes())
    assert len(next_result[0]["dice"]) == 2
    assert a["acid"] == [{"dice": 1, "due": 2}]


def test_upper_floor_corrosion_continues_after_absorbed_hit():
    from lorekit.alien import acid
    from lorekit.alien.state import initial

    class Sixes:
        def randint(self, low, high):
            return 6

    state = initial()
    state["scene"] = {"zones": {"deck": {"floor_armor": 3}}}
    out = acid.initial_floor(state, "deck", 2, Sixes())
    assert out[0]["armor_after"] == 1
    assert not out[0]["breached"]
    state["clocks"]["round"] += 1
    acid.tick_floors(state, Sixes())
    assert state["scene"]["zones"]["deck"]["floor_armor"] == 0
    state["clocks"]["round"] += 1
    assert acid.tick_floors(state, Sixes())[0]["breached"]
