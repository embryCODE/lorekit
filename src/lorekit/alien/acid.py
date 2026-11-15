"""Acid attacks and ongoing damage. B17: detailed-prose ruling selected by Mason."""

from __future__ import annotations

import math

from lorekit.alien import survival
from lorekit.alien.state import actor, integer


def splash(state, target_id, dice_count, bundle, rng):
    a = actor(state, target_id)
    integer(dice_count, "acid dice", 1)
    if a.get("acid_proof"):
        return {"type": "acid", "actor": target_id, "immune": True}
    dice = [rng.randint(1, 6) for _ in range(dice_count)]
    successes = dice.count(6)
    raw = successes  # Base damage 1 + one per extra success.
    before_armor = a["armor"]
    health_damage = max(0, raw - before_armor)
    # Armor corrosion uses points of the hit, including absorbed points.
    a["armor"] = max(0, before_armor - raw)
    events = []
    if health_damage:
        # Mitigation already occurred using the armor level before corrosion.
        events = survival.damage(state, target_id, health_damage, bundle, rng, bypass=True)
    return {
        "type": "acid",
        "actor": target_id,
        "dice": dice,
        "raw_damage": raw,
        "armor_before": before_armor,
        "armor_after": a["armor"],
        "health_damage": health_damage,
        "events": events,
        "continues": raw > 0,
    }


def initial(state, target_id, dice_count, bundle, rng):
    result = splash(state, target_id, dice_count, bundle, rng)
    if result.get("continues"):
        a = actor(state, target_id)
        a.setdefault("acid", []).append({"dice": math.ceil(dice_count / 2), "due": state["clocks"]["round"] + 1})
    return result


def tick(state, target_id, bundle, rng):
    a = actor(state, target_id)
    events = []
    remaining = []
    for effect in a.get("acid", []):
        if effect["due"] > state["clocks"]["round"]:
            remaining.append(effect)
            continue
        result = splash(state, target_id, effect["dice"], bundle, rng)
        events.append(result)
        if result.get("continues") and not a["dead"]:
            remaining.append({"dice": math.ceil(effect["dice"] / 2), "due": state["clocks"]["round"] + 1})
    a["acid"] = remaining
    return events
