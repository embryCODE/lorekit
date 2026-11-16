"""Environmental attacks, radiation and zone fire progression."""

from __future__ import annotations

from lorekit.alien import checks, effects, scenes, survival
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def environmental_damage(state, aid, amount, bundle, rng, *, kind, armor=True):
    a = actor(state, aid)
    actual = max(0, amount - (a["armor"] if armor else 0))
    if a["species"] != "human":
        events = survival.damage(state, aid, amount, bundle, rng, bypass=not armor, fire=kind == "fire")
        return {
            "type": "environmental_damage",
            "actor": aid,
            "kind": kind,
            "incoming": amount,
            "inflicted": events[0]["inflicted"],
            "events": events,
        }
    actual, resistance_dice = survival.resist(a, actual, bundle, rng)
    a["health"] = max(0, a["health"] - actual)
    if a["health"] == 0 and actual:
        effects.stop_panic(a)
        hazards = a.setdefault("hazards", [])
        if not any(h["id"] == kind for h in hazards):
            hazards.append(
                {
                    "id": kind,
                    "kind": "environmental_death",
                    "unit": "round",
                    "penalty": 0,
                    "due": state["clocks"]["round"] + 1,
                }
            )
    return {
        "type": "environmental_damage",
        "actor": aid,
        "kind": kind,
        "incoming": amount,
        "inflicted": actual,
        "resistance_dice": resistance_dice,
    }


def fire_attack(state, aid, intensity, bundle, rng):
    integer(intensity, "fire intensity", 1)
    dice = [rng.randint(1, 6) for _ in range(intensity)]
    a = actor(state, aid)
    if a["species"] == "xenomorph":
        events = survival.damage(state, aid, dice.count(6), bundle, rng, fire=True)
        actual = events[0]["inflicted"]
    else:
        event = environmental_damage(state, aid, dice.count(6), bundle, rng, kind="fire")
        events = [event]
        actual = event["inflicted"]
    if actual:
        a["burning"] = intensity
    elif a["zone"] not in state["scene"].get("fires", {}):
        a.pop("burning", None)
        a["hazards"] = [h for h in a.get("hazards", []) if h["id"] != "fire"]
    return {"type": "fire_attack", "actor": aid, "dice": dice, "events": events}


def round_start(state, bundle, rng):
    if not state["scene"]:
        return []
    fires = state["scene"].setdefault("fires", {})
    events = []
    for zone, intensity in list(fires.items()):
        die = rng.randint(1, 6)
        if die <= 2:
            del fires[zone]
        elif die >= 5:
            targets = []
            for edge in state["scene"]["borders"]:
                if zone in (edge["a"], edge["b"]) and edge.get("open") and not edge.get("sealed"):
                    other = edge["b"] if edge["a"] == zone else edge["a"]
                    if state["scene"]["zones"][other].get("flammable"):
                        targets.append(other)
            if targets:
                fires[rng.choice(sorted(targets))] = intensity
        events.append({"type": "fire_spread", "zone": zone, "die": die})
    for aid, a in sorted(state["actors"].items()):
        if not a["dead"] and (a.get("burning") or a["zone"] in fires):
            intensity = max(a.get("burning", 0), fires.get(a["zone"], 0))
            events.append(fire_attack(state, aid, intensity, bundle, rng))
    return events


def radiation(state, intent, bundle, rng):
    a = actor(state, intent.get("actor"))
    level = intent.get("level")
    if level not in ("weak", "strong", "extreme"):
        raise LoreKitError("Radiation level must be weak, strong or extreme")
    index = ("weak", "strong", "extreme").index(level)
    if a["species"] == "xenomorph":
        index -= 1
        if index < 0:
            return {"type": "radiation_immune", "actor": intent["actor"]}
    a["hotspot"] = ("weak", "strong", "extreme")[index]
    a["radiation_level"] = level
    a["radiation_unit"] = ("shift", "stretch", "round")[index]
    a["radiation_due"] = state["clocks"][a["radiation_unit"]] + 1
    a["rads"] += 1
    dice = [rng.randint(1, 6) for _ in range(a["rads"])]
    event = environmental_damage(state, intent["actor"], dice.count(6), bundle, rng, kind="radiation", armor=False)
    return {"type": "radiation", "actor": intent["actor"], "rads": a["rads"], "dice": dice, "events": [event]}


def act(state, intent, bundle, rng):
    action = intent.get("action")
    if action == "blast":
        zone = intent.get("zone")
        if not state["scene"] or zone not in state["scene"]["zones"]:
            raise LoreKitError("Unknown blast zone")
        power = integer(intent.get("power"), "blast power", 1)
        base = (power + 2) // 3
        dice = [rng.randint(1, 6) for _ in range(power)]
        hits = dice.count(6)
        events = []
        if hits:
            for tid, target in sorted(state["actors"].items()):
                if target["zone"] == zone and not target["dead"]:
                    events.extend(
                        survival.damage(
                            state,
                            tid,
                            base + hits - 1,
                            bundle,
                            rng,
                            base_damage=base,
                            barrier_armor=target.get("barrier_armor", 0) if target.get("cover") else 0,
                        )
                    )
        events.extend(effects.drain(state, rng, bundle=bundle))
        return {"type": "blast", "zone": zone, "dice": dice, "base_damage": base, "events": events}
    if action == "fire_set":
        zone = intent.get("zone")
        if not state["scene"] or zone not in state["scene"]["zones"]:
            raise LoreKitError("Unknown fire zone")
        intensity = integer(intent.get("intensity"), "fire intensity", 1)
        state["scene"].setdefault("fires", {})[zone] = intensity
        return {"type": "fire_started", "zone": zone, "intensity": intensity}
    if action == "radiation":
        return radiation(state, intent, bundle, rng)
    if action == "leave_radiation":
        a = actor(state, intent.get("actor"))
        a.pop("hotspot", None)
        a["hazards"] = [h for h in a.get("hazards", []) if h["id"] != "radiation"]
        return {"type": "radiation_left", "actor": intent["actor"]}
    if action == "fall":
        a = actor(state, intent.get("actor"))
        height = integer(intent.get("meters"), "fall meters")
        if state["scene"] and state["scene"]["zones"][a["zone"]].get("zero_g"):
            return {"type": "fall", "damage": 0}
        if intent.get("controlled"):
            return checks.start(
                state,
                {
                    "actor": intent["actor"],
                    "skill": "mobility",
                    "continuation": {"kind": "fall", "actor": intent["actor"], "damage": height // 2},
                },
                bundle,
                rng,
            )
        return {
            "type": "fall",
            "events": survival.damage(state, intent["actor"], height // 2, bundle, rng, bypass=True),
        }
    if action == "extinguish":
        aid, tid = intent.get("actor"), intent.get("target")
        actor(state, tid)
        if aid != tid and scenes.range_band(state, aid, tid) != 0:
            raise LoreKitError("Extinguishing requires Adjacent range")
        scenes.spend(state, aid, "full")
        return checks.start(
            state,
            {"actor": aid, "skill": "mobility", "continuation": {"kind": "extinguish", "target": tid}},
            bundle,
            rng,
        )
    raise LoreKitError("Unsupported environmental operation")
