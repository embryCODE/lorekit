"""Explicit dose use, shift overdoses and time-limited physiological effects."""

from __future__ import annotations

from lorekit.alien import effects, scenes
from lorekit.alien.state import actor
from lorekit.db import LoreKitError

KINDS = {
    "wake_day",
    "hypersleep_relief",
    "stress_zero",
    "coffee",
    "alcohol",
    "food",
    "water",
    "nutrition",
    "meal_relief",
    "recreation_up",
    "recreation_down",
}


def fatigue(a):
    sources = {"air", "hunger", "cold", "heat", "sleep", "forced_sleep", "disease", "hypersleep"}
    return any(h["kind"] in sources or h["id"] in sources for h in a.get("hazards", []))


def relieve(state, aid, amount, *, alcohol=False):
    a = actor(state, aid)
    traits = a.get("trauma_traits", {})
    if a.get("no_stress_recovery_until_day", -1) > state["clocks"]["day"]:
        return False
    if traits.get("rest_alcohol") and not alcohol:
        return False
    if traits.get("rest_alone") and any(
        other is not a and not other["dead"] and other["zone"] == a["zone"] for other in state["actors"].values()
    ):
        return False
    a["stress"] = max(0, a["stress"] - amount)
    return True


def consume(state, intent, bundle):
    from lorekit.alien import survival

    aid, tid = intent.get("actor"), intent.get("target", intent.get("actor"))
    helper, a = actor(state, aid), actor(state, tid)
    entry, definition = survival.item(helper, intent.get("item"), bundle)
    kind = definition.get("consume")
    if kind not in KINDS or entry.get("dropped") or a["species"] != "human" or a["dead"]:
        raise LoreKitError("Choose an available supported dose/food and a living human recipient")
    if aid != tid and scenes.range_band(state, aid, tid) != 0:
        raise LoreKitError("Administering a dose requires Adjacent range")
    cost = intent.get("cost", "quick")
    if cost not in ("quick", "full"):
        raise LoreKitError("Choose a quick/full consumption cost")
    scenes.spend(state, aid, cost)
    entry["quantity"] -= 1
    if not entry["quantity"]:
        entry["equipped"] = False
    doses = a.setdefault("doses", {})
    group = definition.get("dose_group", kind)
    old = doses.get(group, {})
    count = old.get("count", 0) + 1 if old.get("shift") == state["clocks"]["shift"] else 1
    doses[group] = {"shift": state["clocks"]["shift"], "count": count}
    if kind in ("stress_zero", "alcohol", "recreation_up", "recreation_down") and count > 1:
        if a.get("dose_penalty_shift") != state["clocks"]["shift"]:
            a["dose_penalty"] = 0
        a["dose_penalty"] = a.get("dose_penalty", 0) + 1
        a["dose_penalty_shift"] = state["clocks"]["shift"]
    result = {"type": "consumed", "actor": tid, "item": entry["id"], "dose": count, "kind": kind}
    if kind == "wake_day":
        effects.stress(a, 1)
        a["no_stress_recovery_until_day"] = state["clocks"]["day"] + 1
        a["sleep_suppressed_day"] = state["clocks"]["day"] + 1
        suppress_sleep(a)
    elif kind == "hypersleep_relief":
        a["hazards"] = [h for h in a.get("hazards", []) if h["id"] != "hypersleep"]
        a["fatigued"] = fatigue(a)
    elif kind == "stress_zero":
        result["stress_relief"] = relieve(state, tid, a["stress"])
    elif kind == "coffee" and count == 1:
        effects.stress(a, 1)
        a["sleep_suppressed_shift"] = state["clocks"]["shift"] + 1
        suppress_sleep(a)
    elif kind in ("alcohol", "recreation_down", "meal_relief"):
        result["stress_relief"] = relieve(state, tid, 1, alcohol=kind == "alcohol")
    elif kind == "recreation_up":
        effects.stress(a, 1)
    if kind in ("food", "nutrition", "meal_relief"):
        a["food_until_day"] = state["clocks"]["day"] + 1
    if kind in ("water", "nutrition"):
        a["water_until_day"] = state["clocks"]["day"] + 1
    if kind in ("food", "water", "nutrition", "meal_relief"):
        if (
            a.get("food_until_day", -1) > state["clocks"]["day"]
            and a.get("water_until_day", -1) > state["clocks"]["day"]
        ):
            a["hazards"] = [h for h in a.get("hazards", []) if h["id"] != "hunger"]
            a["fatigued"] = fatigue(a)
    return result


def suppress_sleep(a):
    sleep = [h for h in a.get("hazards", []) if h["id"] == "sleep"]
    if sleep:
        a["suppressed_sleep"] = {**sleep[0], "kind": "sleep", "unit": "shift"}
        a["sleep_was_fatigued"] = a["fatigued"]
        a["unconscious"] = False
        a["hazards"] = [h for h in a["hazards"] if h["id"] != "sleep"]
        a["fatigued"] = fatigue(a)


def expire(state, a):
    now_shift, now_day = state["clocks"]["shift"], state["clocks"]["day"]
    if a.get("dose_penalty_shift", now_shift) < now_shift:
        a.pop("dose_penalty", None)
        a.pop("dose_penalty_shift", None)
    if a.get("sleep_suppressed_day", now_day + 1) <= now_day:
        a.pop("sleep_suppressed_day")
        a.setdefault("suppressed_sleep", {"id": "sleep", "kind": "sleep", "unit": "shift", "penalty": 0})
    if a.get("sleep_suppressed_shift", now_shift + 1) <= now_shift:
        a.pop("sleep_suppressed_shift")
    protected = a.get("sleep_suppressed_day", -1) > now_day or a.get("sleep_suppressed_shift", -1) > now_shift
    if not protected and a.get("suppressed_sleep"):
        hazard = a.pop("suppressed_sleep")
        # Coffee postpones by exactly one shift; it does not erase deprivation.
        hazard["due"] = now_shift
        a.setdefault("hazards", []).append(hazard)
        a["fatigued"] |= a.pop("sleep_was_fatigued", False)
