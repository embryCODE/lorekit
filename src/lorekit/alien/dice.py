"""Pure, explicit-RNG Alien Evolved skill pools (printed rules pp. 42–43)."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class PoolRoll:
    base: tuple[int, ...]
    stress: tuple[int, ...]

    @property
    def successes(self) -> int:
        return self.base.count(6) + self.stress.count(6)

    @property
    def stress_symbols(self) -> int:
        return self.stress.count(1)


def skill_roll(
    rng: random.Random,
    *,
    attribute: int,
    skill: int = 0,
    modifier: int = 0,
    helpers: int = 0,
    stress: int = 0,
) -> PoolRoll:
    """Roll a validated pool; caller selects attribute and validates helper eligibility.

    Modifier combines gear, difficulty and effects. Help action spending belongs
    to the transactional scene layer, not this stateless primitive.
    """
    values = (attribute, skill, modifier, helpers, stress)
    if any(type(value) is not int for value in values):
        raise ValueError("pool inputs must be integers")
    if attribute < 0 or skill < 0 or stress < 0:
        raise ValueError("attribute, skill and stress must be nonnegative")
    if not 0 <= helpers <= 3:
        raise ValueError("helpers must be between zero and three")
    count = max(1, attribute + skill + modifier + helpers)
    return PoolRoll(
        tuple(rng.randint(1, 6) for _ in range(count)),
        tuple(rng.randint(1, 6) for _ in range(stress)),
    )
