"""Rule boundaries for Alien's separate base/stress pools."""

import random

import pytest

from lorekit.alien.dice import PoolRoll, skill_roll


def test_stress_successes_and_symbols_are_separate():
    roll = PoolRoll((1, 6, 2), (1, 6, 6))
    assert roll.successes == 3
    assert roll.stress_symbols == 1


def test_penalties_floor_only_the_base_pool():
    roll = skill_roll(random.Random(7), attribute=2, modifier=-20, stress=4)
    assert len(roll.base) == 1
    assert len(roll.stress) == 4


def test_helpers_and_gear_add_base_dice_and_seed_reproduces_faces():
    args = dict(attribute=3, skill=2, helpers=3, modifier=2, stress=2)
    first = skill_roll(random.Random(8), **args)
    assert len(first.base) == 10
    assert first == skill_roll(random.Random(8), **args)


@pytest.mark.parametrize("overrides", [{"helpers": 4}, {"stress": -1}, {"skill": True}])
def test_invalid_input_does_not_consume_rng(overrides):
    rng = random.Random(11)
    before = rng.getstate()
    with pytest.raises(ValueError):
        skill_roll(rng, attribute=3, **overrides)
    assert rng.getstate() == before
