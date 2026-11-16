"""Environmental attacks, radiation and zone fire progression."""

from __future__ import annotations

import copy

from lorekit.alien import checks, effects, scenes, survival
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def environmental_damage(state, aid, amount, bundle, rng, *, kind, armor=True, death_unit="round"):
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
    before_health = a["health"]
    a["health"] = max(0, a["health"] - actual)
    if a["health"] == 0 and actual:
        effects.stop_panic(a)
        hazards = a.setdefault("hazards", [])
        if not any(h["id"] == kind for h in hazards):
            hazards.append(
                {
                    "id": kind,
                    "kind": "environmental_death",
                    "unit": death_unit,
                    "penalty": 0,
                    "due": state["clocks"][death_unit] + 1,
                }
            )
    effects.broken_witnesses(state, aid, before_health)
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
    timer = state["scene"].get("countdown")
    if timer and timer["minutes"]:
        dice = [rng.randint(1, 6) for _ in range(min(6, timer["minutes"]))]
        timer["minutes"] = max(0, timer["minutes"] - dice.count(1))
        events.append(
            {"type": "countdown", "dice": dice, "minutes": timer["minutes"], "expired": timer["minutes"] == 0}
        )
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
            a.pop("hotspot", None)
            a["hazards"] = [h for h in a.get("hazards", []) if h["id"] != "radiation"]
            return {"type": "radiation_immune", "actor": intent["actor"]}
    a["hotspot"] = ("weak", "strong", "extreme")[index]
    a["radiation_level"] = level
    a["radiation_unit"] = ("shift", "stretch", "round")[index]
    a["radiation_due"] = state["clocks"][a["radiation_unit"]] + 1
    for hazard in a.get("hazards", []):
        if hazard["id"] == "radiation":
            hazard.update(unit=a["radiation_unit"], due=a["radiation_due"])
    a["rads"] += 1
    dice = [rng.randint(1, 6) for _ in range(a["rads"])]
    event = environmental_damage(
        state,
        intent["actor"],
        dice.count(6),
        bundle,
        rng,
        kind="radiation",
        armor=False,
        death_unit=a["radiation_unit"],
    )
    return {"type": "radiation", "actor": intent["actor"], "rads": a["rads"], "dice": dice, "events": [event]}


def act(state, intent, bundle, rng):
    action = intent.get("action")
    if action == "countdown_set":
        if not state["scene"]:
            raise LoreKitError("A countdown requires an active scene")
        minutes = integer(intent.get("minutes"), "countdown minutes", 1)
        state["scene"]["countdown"] = {"minutes": minutes}
        return {"type": "countdown_set", "minutes": minutes}
    if action == "shoot_hull":
        aid = intent.get("actor")
        a = actor(state, aid)
        surface = state["scene"]["zones"][a["zone"]] if state["scene"] else {}
        if "outer_armor" not in surface:
            raise LoreKitError("Map an outer bulkhead armor level first")
        entry, definition = survival.item(a, intent.get("weapon"), bundle)
        if (
            not entry.get("equipped")
            or entry.get("dropped")
            or "damage" not in definition
            or not definition.get("supply_type")
            or not entry["supply"]
        ):
            raise LoreKitError("Choose an available, loaded ranged weapon")
        scenes.spend(state, aid, "full")
        return checks.start(
            state,
            {
                "actor": aid,
                "skill": "ranged_combat",
                "modifier": definition.get("modifier", 0) + 2,
                "continuation": {"kind": "shoot_hull", "actor": aid, "item": entry["id"], "zone": a["zone"]},
            },
            bundle,
            rng,
        )
    if action == "breach":
        if type(intent.get("automatic_seal", False)) is not bool:
            raise LoreKitError("automatic_seal must be boolean")
        return breach(state, intent.get("zones"), intent.get("automatic_seal", False))
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


def breach(state, zones, automatic_seal=False):
    if (
        not state["scene"]
        or not isinstance(zones, list)
        or not zones
        or len(set(zones)) != len(zones)
        or any(zone not in state["scene"]["zones"] for zone in zones)
    ):
        raise LoreKitError("Declare distinct affected breach zones in venting order")
    vents = state["scene"].setdefault("vents", {})
    for index, zone in enumerate(zones, 1):
        vents[zone] = {"vacuum_due": state["clocks"]["stretch"] + index, "vacuum": False}
        if automatic_seal:
            for edge in state["scene"]["borders"]:
                if zone in (edge["a"], edge["b"]) and edge.get("door"):
                    edge.update(sealed=True, open=False)
    return {"type": "hull_breach", "zones": zones, "automatic_seal": automatic_seal}


def vent_tick(state):
    events = []
    if state["scene"]:
        for zone, vent in state["scene"].get("vents", {}).items():
            if not vent["vacuum"] and vent["vacuum_due"] <= state["clocks"]["stretch"]:
                vent["vacuum"] = True
                events.append({"type": "zone_vacuum", "zone": zone})
    return events


def pressure_guard(state, intent, bundle, rng, *, after=None):
    """Resolve the action-free pressure check before executing the saved intent."""
    op = intent.get("op")
    if op not in {
        "attack",
        "action",
        "recover",
        "check",
        "move",
        "approach",
        "creature_act",
        "creature_move",
        "end_turn",
        "talent_use",
    }:
        return None
    if op == "check" and intent.get("passive"):
        return None
    aid = intent.get("actor")
    a = actor(state, aid)
    if a["dead"] or a["species"] != "human":
        return None
    vent = (state["scene"] or {}).get("vents", {}).get(a["zone"])
    vacuum_hazard = next((h for h in a.get("hazards", []) if h["id"] == "vacuum"), None)
    if not vent and not vacuum_hazard:
        return None
    vacuum = bool(vacuum_hazard) or vent["vacuum"]
    if vacuum and a.get("vacuum_proof"):
        return None
    token = (
        f"combat:{state['combat']['round']}:{state['combat']['index']}"
        if state["combat"]
        else f"round:{state['clocks']['round']}"
        if vacuum
        else f"stretch:{state['clocks']['stretch']}"
    )
    token += ":vacuum" if vacuum else ":venting"
    if a.get("pressure_token") == token:
        if a.get("pressure_failed") and op != "end_turn":
            raise LoreKitError("Failed pressure check prevents actions until the next turn")
        return None
    from lorekit.alien.engine import transition

    if after is None:
        preview = copy.deepcopy(state)
        preview["actors"][aid].update(pressure_token=token, pressure_failed=False)
        transition(preview, copy.deepcopy(intent), copy.deepcopy(rng))
    a["pressure_token"] = token
    a["pressure_failed"] = False
    penalty = a.get("vacuum_checks", 0) if vacuum else 0
    if vacuum:
        effects.stress(a, 1)
        a["vacuum_checks"] = penalty + 1
    return checks.start(
        state,
        {
            "actor": aid,
            "skill": "survival",
            "modifier": -penalty,
            "continuation": {"kind": "pressure", "actor": aid, "vacuum": vacuum, "intent": intent, "after": after},
        },
        bundle,
        rng,
    )


def finish_pressure(state, result, bundle, rng):
    from lorekit.alien.engine import transition

    c = result["continuation"]
    a = actor(state, c["actor"])
    if result["success"]:
        if c.get("after"):
            from lorekit.alien import forced_movement
            from lorekit.alien.engine import complete_check

            return complete_check(state, forced_movement.run(state, c["after"]["plan"], bundle, rng), bundle, rng)
        resolved = transition(state, c["intent"], rng)
        resolved["pressure_check"] = result["checks"]
        return resolved
    a["pressure_failed"] = True
    if c["vacuum"]:
        before_health = a["health"]
        a["health"] = 0
        effects.broken_witnesses(state, c["actor"], before_health)
        effects.stop_panic(a)
        a.setdefault("hazards", [])
        a["hazards"] = [h for h in a["hazards"] if h["id"] != "vacuum"]
        a["hazards"].append(
            {
                "id": "vacuum",
                "kind": "environmental_death",
                "unit": "round",
                "penalty": 0,
                "due": state["clocks"]["round"] + 1,
            }
        )
    return {"type": "pressure_failed", "actor": c["actor"], "vacuum": c["vacuum"], "checks": result["checks"]}


def finish_hull_shot(state, result, bundle, rng):
    c = result["continuation"]
    a = actor(state, c["actor"])
    entry, definition = survival.item(a, c["item"], bundle)
    surface = state["scene"]["zones"][c["zone"]]
    armor = max(0, surface["outer_armor"] - int(definition.get("piercing", False)))
    amount = definition["damage"] + result["successes"][0] - 1
    events = []
    if result["success"] and definition["damage"] >= armor and amount > armor:
        events.append(breach(state, [c["zone"]], surface.get("auto_seal", False)))
    if definition.get("single_shot"):
        entry["supply"] = 0
    else:
        events.append(survival.supply(a, entry["id"], bundle, rng))
    return {"type": "hull_shot", "actor": c["actor"], "checks": result["checks"], "events": events}
