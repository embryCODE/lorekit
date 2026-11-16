"""Supplies, damage and recovery primitives; injury content is supplied locally."""

from __future__ import annotations

import copy
import re

from lorekit.alien import effects
from lorekit.alien.characters import talents
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def item(a, instance_id, bundle):
    entry = next((i for i in a["inventory"] if i["id"] == instance_id), None)
    if entry is None or entry.get("quantity", 0) <= 0:
        raise LoreKitError("Inventory instance not found or consumed")
    definition = bundle["collections"].get("items", {}).get(entry["content_id"])
    if definition is None:
        raise LoreKitError("Required item content is missing")
    return entry, definition


def supply(a, instance_id, bundle, rng, *, npc_roll=False):
    entry, definition = item(a, instance_id, bundle)
    if definition.get("supply_type") not in ("air", "ammo", "power"):
        raise LoreKitError("Item does not track a supported supply")
    current = integer(entry["supply"], "current supply")
    if not current:
        return {"type": "supply", "item": instance_id, "dice": [], "remaining": 0}
    reduction = sum(
        e["value"]
        for e in talents(a, bundle)
        if e["hook"] == "supply_reduction" and e.get("resource") == definition["supply_type"]
    )
    if a["kind"] == "npc" and not npc_roll:
        dice = []
        used = 1
    else:
        dice = [rng.randint(1, 6) for _ in range(min(6, max(1, current - reduction)))]
        used = dice.count(1)
    entry["supply"] = max(0, current - used)
    if definition.get("unreliable") and used:
        entry["jammed"] = True
    return {"type": "supply", "item": instance_id, "dice": dice, "remaining": entry["supply"]}


def injury(state, aid, bundle, rng, *, forced=None):
    a = actor(state, aid)
    entries = bundle["collections"].get("injuries", {})
    outcomes = {str(t * 10 + u) for t in range(1, 7) for u in range(1, 7)}
    if set(entries) != outcomes:
        raise LoreKitError("Damage requires a complete local D66 injury table")
    dice_log = []
    existing = {i["id"] for i in a["injuries"]}
    if forced is not None and str(forced) not in entries:
        raise LoreKitError("Missing signature-specific critical injury")
    while True:
        dice = [forced // 10, forced % 10] if forced is not None else [rng.randint(1, 6), rng.randint(1, 6)]
        dice_log.append(dice)
        value = dice[0] * 10 + dice[1]
        if forced is not None or value < 14 or str(value) not in existing:
            break
        if len(dice_log) > 1000:
            raise LoreKitError("Injury reroll safety limit reached; retry with an explicit ruling")
    if forced is None and any(e["hook"] == "lucky" for e in talents(a, bundle)):
        second_log = []
        for _ in range(1000):
            dice = [rng.randint(1, 6), rng.randint(1, 6)]
            second_log.append(dice)
            second = dice[0] * 10 + dice[1]
            if second < 14 or str(second) not in existing:
                break
        else:
            raise LoreKitError("Lucky injury reroll limit reached")
        state["queue"].append(
            {"type": "lucky_injury", "actor": aid, "values": [value, second], "dice": [dice_log, second_log]}
        )
        return {"type": "critical_injury_choice_queued", "actor": aid, "values": [value, second]}
    return apply_injury(state, aid, bundle, rng, value, dice_log)


def apply_injury(state, aid, bundle, rng, value, dice_log):
    a = actor(state, aid)
    definition = bundle["collections"]["injuries"][str(value)]
    interval = definition.get("interval")
    entry = {
        "id": str(value),
        "effects": copy.deepcopy(definition.get("effects", [])),
        "lethal": definition.get("lethal", False),
        "stabilized": False,
        "successes": 0,
        "interval": interval,
        "due": (
            a.get("turns_completed", 0) if interval == "round" and state["combat"] else state["clocks"].get(interval, 0)
        )
        + 1
        if interval
        else None,
        "due_unit": "turn" if interval == "round" and state["combat"] else interval,
        "surgery_required": definition.get("surgery", False),
    }
    healing = definition.get("healing")
    if healing:
        expression = healing["amount"]
        match = re.fullmatch(r"([1-4])d6", expression) if isinstance(expression, str) else None
        if match:
            healing_dice = [rng.randint(1, 6) for _ in range(int(match[1]))]
            entry["healing_remaining"] = sum(healing_dice)
            entry["healing_dice"] = healing_dice
        else:
            entry["healing_remaining"] = integer(expression, "healing duration", 1)
        entry["healing_unit"] = healing["unit"]
        entry["prosthesis_required"] = definition.get("prosthesis", False)
    if definition.get("instant_death"):
        a["dead"] = True
    a["injuries"].append(entry)
    for e in entry["effects"]:
        if e["hook"] == "relieve_stress":
            a["stress"] = 0
        elif e["hook"] == "stress_gain":
            effects.stress(a, e["value"])
        elif e["hook"] == "infection":
            state["queue"].append({"type": "injury_infection", "actor": aid, "penalty": e.get("value", 0)})
        elif e["hook"] == "trauma" and state["options"]["mental_trauma"]:
            state["queue"].append({"type": "direct_trauma", "actor": aid})
    return {"type": "critical_injury", "actor": aid, "dice": dice_log, "injury": str(value)}


def damage(
    state,
    aid,
    amount,
    bundle,
    rng,
    *,
    piercing=False,
    weak_spot=False,
    bypass=False,
    base_damage=None,
    barrier_armor=0,
    fire=False,
):
    a = actor(state, aid)
    integer(amount, "damage")
    integer(barrier_armor, "barrier armor")
    if a["dead"]:
        raise LoreKitError("Cannot damage a dead actor")
    reduction = int(piercing) + int(weak_spot)
    barrier = max(0, barrier_armor - reduction)
    if barrier_armor and integer(base_damage, "base damage") < barrier:
        amount = 0
    else:
        amount = max(0, amount - barrier)
    armor_base = a["armor"]
    if fire and a["species"] == "xenomorph":
        armor_base = min(armor_base, bundle["collections"]["creatures"][a["content_id"]].get("armor_fire", armor_base))
    armor = 0 if bypass else max(0, armor_base - reduction)
    actual = max(0, amount - armor)
    actual, resistance_dice = resist(a, actual, bundle, rng)
    before = a["health"]
    events = [{"type": "damage", "actor": aid, "incoming": amount, "armor": armor, "inflicted": actual}]
    if resistance_dice:
        events[0]["resistance_dice"] = resistance_dice
    if not actual:
        return events
    a["health"] = max(0, before - actual)
    if a["species"] == "human" and actual >= 2 * a["health_max"]:
        a["dead"] = True
    elif a["health"] == 0:
        effects.stop_panic(a)
        a.pop("overkill", None)
        if a["species"] == "xenomorph":
            from lorekit.alien.creatures import broken

            definition = bundle["collections"]["creatures"][a["content_id"]]
            events.append(broken(state, aid, rng, definition))
        elif a["species"] == "synthetic":
            value = rng.randint(1, 6)
            rolled = value
            existing = a.setdefault("system_damage", [])
            while value in existing and value < 6:
                value += 1
            if value not in existing:
                existing.append(value)
            if value == 6:
                a["shutdown"] = True
            if value == 1:
                a["skip_turn"] = True
                a["interrupt_blocked"] = True
            events.append({"type": "system_damage", "actor": aid, "die": rolled, "result": value})
        else:
            events.append(injury(state, aid, bundle, rng))
    if before > 0 and a["health"] == 0:
        a.pop("self_rally_attempted", None)
    if before > 0 and a["health"] == 0 and a["species"] == "human" and a["kind"] == "pc":
        from lorekit.alien.scenes import line_of_sight

        for other_id, other in sorted(state["actors"].items()):
            if other_id != aid and effects.eligible(other) and line_of_sight(state, other_id, aid):
                effects.enqueue_panic(state, other_id, "witnessed_pc_broken")
    if a["species"] == "xenomorph" and not fire:
        from lorekit.alien.acid import initial, initial_floor

        rating = bundle["collections"]["creatures"][a["content_id"]].get("acid", 0)
        if rating:
            events.extend(initial_floor(state, a["zone"], rating + min(actual, a["health_max"]), rng))
            for target_id in a.get("adjacent", []):
                if not actor(state, target_id)["dead"]:
                    events.append(initial(state, target_id, rating + min(actual, a["health_max"]), bundle, rng))
    return events


def restore(a, amount):
    if a["dead"] or a["species"] == "synthetic" or a["fatigued"] or a.get("hotspot"):
        raise LoreKitError("This actor cannot recover human Health")
    a["health"] = min(a["health_max"], a["health"] + amount)


def rest(state, intent, bundle, rng):
    if state["mode"] == "combat":
        raise LoreKitError("End combat before safe rest")
    if intent.get("safe") is not True:
        raise LoreKitError("Safe rest requires explicit safe-area confirmation")
    ids = intent.get("actors", [intent.get("actor")])
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
        raise LoreKitError("Rest requires distinct actor IDs")
    participants = [actor(state, aid) for aid in ids]
    if any(a["dead"] for a in participants):
        raise LoreKitError("Dead actor cannot rest")
    if intent.get("interrupted", False):
        return {"type": "rest_interrupted", "actors": ids}
    removals = intent.get("remove_responses", {})
    if len(ids) == 1 and "remove_response" in intent:
        removals = {ids[0]: intent["remove_response"]}
    can_relieve = {}
    for aid, a in zip(ids, participants, strict=True):
        traits = a.get("trauma_traits", {})
        alone = not any(
            other is not a and other["zone"] == a["zone"] and not other["dead"] for other in state["actors"].values()
        )
        allowed = not (
            traits.get("rest_alone") and not alone or traits.get("rest_alcohol") and intent.get("alcohol") is not True
        )
        allowed &= a.get("no_stress_recovery_until_day", -1) <= state["clocks"]["day"]
        allowed &= a.get("counselor_stretch", -1) < state["clocks"]["stretch"]
        can_relieve[aid] = allowed
        if allowed and a["stress_responses"] and removals.get(aid) not in a["stress_responses"]:
            raise LoreKitError("Choose one active stress response to remove per affected actor")
    advance_time(state, "stretch", 1)
    for aid, a in zip(ids, participants, strict=True):
        if a["species"] == "human" and not a["fatigued"] and not a.get("hotspot"):
            a["health"] = min(a["health_max"], a["health"] + 1)
        if can_relieve[aid]:
            aura = max(
                [0]
                + [
                    e["value"]
                    for other in state["actors"].values()
                    if other["zone"] == a["zone"] and not other["dead"]
                    for e in talents(other, bundle)
                    if e["hook"] == "rest_bonus"
                ]
            )
            a["stress"] = max(0, a["stress"] - 1 - aura)
            if a["stress_responses"]:
                a["stress_responses"].remove(removals[aid])
    return {"type": "rest", "actors": ids, "events": effects.drain(state, rng, bundle=bundle)}


def advance_time(state, unit, amount):
    if unit not in state["clocks"]:
        raise LoreKitError("Unknown logical time unit")
    integer(amount, "elapsed units", 1, 100)
    if state["mode"] == "combat" and unit == "round":
        raise LoreKitError("Combat rounds advance through end_turn")
    # One unit per command keeps intervening decisions on their actual deadline.
    if amount != 1:
        raise LoreKitError("Advance one unit at a time so due decisions cannot be skipped")
    state["clocks"][unit] += 1
    due_events(state, unit)
    return {"type": "time_advanced", "unit": unit, "clock": state["clocks"][unit]}


def due_events(state, unit):
    now = state["clocks"][unit]
    for aid, a in sorted(state["actors"].items()):
        if a["dead"]:
            continue
        for injury in list(a["injuries"]):
            if (
                injury.get("healing_unit") == unit
                and not injury.get("surgery_required")
                and not injury.get("prosthesis_required")
            ):
                injury["healing_remaining"] -= 2 if unit == "day" and a.get("nursed_until_day") == now else 1
                if injury["healing_remaining"] <= 0:
                    a["injuries"].remove(injury)
        hug = a.get("facehug")
        if hug and unit == "stretch" and hug["stage"] == "attached" and not hug["inseminated"]:
            state["queue"].append({"type": "infection", "kind": "exposure", "actor": aid})
        if hug and unit == "shift" and hug["shift_due"] <= now:
            state["queue"].append({"type": "infection", "kind": "shift", "actor": aid})
        if unit == "shift" and a.get("mature_due", now + 1) <= now:
            state["queue"].append({"type": "mature", "actor": aid})
        if a.get("hotspot") and a.get("radiation_unit") == unit and a["radiation_due"] <= now:
            state["queue"].append({"type": "radiation", "actor": aid})
            a["radiation_due"] = now + 1
        elif unit == "shift" and not a.get("hotspot"):
            a["rads"] = max(0, a["rads"] - 1)
        if unit == "stretch":
            effects.stop_panic(a)
            for inventory in a["inventory"]:
                # Active air supplies are explicit equipped items, not every spare tank.
                if inventory.get("equipped") and inventory.get("active_air"):
                    state["queue"].append({"type": "due", "kind": "air_supply", "actor": aid, "item": inventory["id"]})
        for i in a["injuries"]:
            if i["lethal"] and not i["stabilized"] and i.get("due_unit", i["interval"]) == unit and i["due"] <= now:
                state["queue"].append({"type": "due", "kind": "death", "actor": aid, "injury": i["id"]})
                i["due"] = now + 1
        for hazard in a.get("hazards", []):
            if hazard["unit"] == unit and hazard["due"] <= now:
                state["queue"].append({"type": "due", "kind": "hazard", "actor": aid, "hazard": hazard["id"]})
                hazard["due"] = now + 1


def due(state, entry, bundle, rng):
    from lorekit.alien import checks

    a = actor(state, entry["actor"])
    if a["dead"]:
        return {"type": "due_skipped", "actor": entry["actor"]}
    if entry["kind"] == "stun_recovery":
        return checks.start(
            state,
            {"actor": entry["actor"], "skill": "stamina", "continuation": {"kind": "stun", "actor": entry["actor"]}},
            bundle,
            rng,
            queued=True,
        )
    if entry["kind"] == "air_supply":
        result = supply(a, entry["item"], bundle, rng)
        sync_air(state, entry["actor"])
        return result
    spec = {"actor": entry["actor"], "continuation": {"kind": "due", "entry": entry}}
    if entry["kind"] == "death":
        if not any(i["id"] == entry["injury"] and not i["stabilized"] for i in a["injuries"]):
            return {"type": "due_skipped"}
        spec.update(
            skill="stamina",
            no_push=True,
            no_stress=True,
            modifier=sum(e["value"] for e in talents(a, bundle) if e["hook"] == "death_modifier"),
        )
    else:
        hazard = next(h for h in a["hazards"] if h["id"] == entry["hazard"])
        if hazard["kind"] == "hypersleep":
            a["hazards"].remove(hazard)
            a["fatigued"] = bool(a["hazards"])
            return {"type": "hypersleep_recovered", "actor": entry["actor"]}
        if hazard["kind"] == "environmental_death":
            spec.update(
                skill="stamina",
                no_push=True,
                no_stress=True,
                modifier=sum(e["value"] for e in talents(a, bundle) if e["hook"] == "death_modifier"),
            )
            return checks.start(state, spec, bundle, rng, queued=True)
        spec.update(skill="stamina" if hazard["kind"] == "disease" else "survival", modifier=-hazard.get("penalty", 0))
        if hazard["kind"] == "vacuum":
            effects.stress(a, 1)
            hazard["penalty"] += 1
    return checks.start(state, spec, bundle, rng, queued=True)


def finish_due(state, result, bundle, rng):
    entry = result["continuation"]["entry"]
    a = actor(state, entry["actor"])
    is_death = entry["kind"] == "death" or (
        entry.get("hazard") is not None
        and any(h["id"] == entry["hazard"] and h["kind"] == "environmental_death" for h in a.get("hazards", []))
    )
    if is_death and not result.get("lucky_chosen") and any(e["hook"] == "lucky" for e in talents(a, bundle)):
        from lorekit.alien import checks

        reroll = checks.roll(
            state,
            {
                "actor": entry["actor"],
                "skill": "stamina",
                "no_push": True,
                "no_stress": True,
                "modifier": sum(e["value"] for e in talents(a, bundle) if e["hook"] == "death_modifier"),
            },
            bundle,
            rng,
        )
        second = {
            **copy.deepcopy(result),
            "checks": [reroll],
            "successes": [checks.successes(reroll)],
            "success": checks.successes(reroll) > 0,
        }
        state["pending"] = {"type": "lucky_death", "actor": entry["actor"], "results": [copy.deepcopy(result), second]}
        return {"type": "lucky_death", "pending": copy.deepcopy(state["pending"]), "events": []}
    success = result["success"]
    events = [{"type": "due_result", "kind": entry["kind"], "actor": entry["actor"], "checks": result["checks"]}]
    if entry["kind"] == "death":
        i = next(i for i in a["injuries"] if i["id"] == entry["injury"])
        if not success:
            a["dead"] = True
        else:
            i["successes"] += 1
            if i["successes"] >= 3:
                i["stabilized"] = True
    else:
        hazard = next(h for h in a["hazards"] if h["id"] == entry["hazard"])
        if hazard["kind"] == "vacuum" and not success:
            a["health"] = 0
            effects.stop_panic(a)
            # Environmental death rolls end only when the source is removed.
            hazard["kind"] = "environmental_death"
        elif hazard["kind"] == "environmental_death":
            if not success:
                a["dead"] = True
        elif hazard["kind"] == "disease" and success:
            a["hazards"].remove(hazard)
            a["fatigued"] = bool(a["hazards"])
        elif not success:
            if a["fatigued"]:
                amount, dice = resist(a, 1, bundle, rng)
                a["health"] = max(0, a["health"] - amount)
                events.append(
                    {"type": "fatigue_damage", "actor": entry["actor"], "inflicted": amount, "resistance_dice": dice}
                )
            a["fatigued"] = True
            effects.stress(a, 1)
            if a["health"] == 0:
                if hazard["kind"] == "sleep":
                    a["unconscious"] = True
                else:
                    hazard["kind"] = "environmental_death"
    return {"type": "due_complete", "events": events}


def hazard(state, intent, bundle, rng):
    a = actor(state, intent.get("actor"))
    kind = intent.get("kind")
    if kind not in ("air", "hunger", "sleep", "cold", "heat", "hypersleep", "vacuum", "disease"):
        raise LoreKitError("Unsupported hazard; explicit GM ruling required")
    if (
        a["species"] == "synthetic"
        or (a["species"] == "xenomorph" and kind in ("cold", "vacuum", "air"))
        or (kind == "vacuum" and a.get("vacuum_proof"))
    ):
        return {"type": "hazard_immune", "actor": intent["actor"]}
    unit = intent.get("unit", "round" if kind in ("air", "vacuum") else "shift")
    if unit not in state["clocks"]:
        raise LoreKitError("Invalid hazard cadence")
    a.setdefault("hazards", [])
    if kind == "disease":
        from lorekit.alien import checks

        return checks.start(
            state,
            {
                "actor": intent["actor"],
                "skill": "stamina",
                "passive": True,
                "modifier": -integer(intent.get("penalty", 0), "virulence"),
                "continuation": {
                    "kind": "disease_exposure",
                    "actor": intent["actor"],
                    "unit": unit,
                    "penalty": intent.get("penalty", 0),
                },
            },
            bundle,
            rng,
        )
    if any(h["id"] == kind for h in a["hazards"]):
        raise LoreKitError("Hazard already active")
    if kind == "hypersleep":
        a["fatigued"] = True
    a["hazards"].append(
        {
            "id": kind,
            "kind": kind,
            "unit": unit,
            "due": state["clocks"][unit] + 1,
            "penalty": integer(intent.get("penalty", 0), "hazard penalty"),
        }
    )
    return {"type": "hazard_applied", "actor": intent["actor"], "kind": kind}


def recover(state, intent, bundle, rng):
    from lorekit.alien import checks, scenes

    method = intent.get("method")
    if method == "rest":
        return rest(state, intent, bundle, rng)
    helper_id, target_id = intent.get("actor"), intent.get("target")
    helper, target = actor(state, helper_id), actor(state, target_id)
    if helper["dead"] or target["dead"]:
        raise LoreKitError("Dead actor cannot give or receive recovery")
    if method not in ("first_aid", "rally", "repair", "surgery", "stop_panic"):
        raise LoreKitError("Unsupported recovery method")
    if target["species"] == "xenomorph":
        raise LoreKitError("Use creature-specific recovery")
    if method in ("first_aid", "rally") and target["species"] != "human":
        raise LoreKitError("Synthetic requires repairs")
    if method == "rally" and target.get("trauma_traits", {}).get("no_rally"):
        raise LoreKitError("This trauma prevents rallying")
    if method == "rally" and target["health"] > 0:
        raise LoreKitError("Only broken actors can be rallied")
    if (
        method == "first_aid"
        and target["health"] > 0
        and not any(i["lethal"] and not i["stabilized"] for i in target["injuries"])
    ):
        raise LoreKitError("First aid cannot heal a healthy actor")
    self_rally = (
        helper_id == target_id and method == "rally" and any(e["hook"] == "self_rally" for e in talents(helper, bundle))
    )
    if self_rally:
        if helper.get("self_rally_attempted"):
            raise LoreKitError("Self-rally has already been attempted for this broken state")
        helper["self_rally_attempted"] = True
    if not self_rally and helper_id == target_id and (method != "first_aid" or helper["health"] == 0):
        if method != "repair":
            raise LoreKitError("This recovery cannot be performed on yourself")
    if (
        method in ("first_aid", "surgery")
        and helper_id != target_id
        and scenes.range_band(state, helper_id, target_id) != 0
    ):
        raise LoreKitError("First aid/surgery requires Adjacent range")
    if method == "rally" and helper["zone"] != target["zone"]:
        raise LoreKitError("Rally target must be in the same zone")
    if method == "stop_panic" and helper["zone"] != target["zone"] and not intent.get("comms"):
        raise LoreKitError("Stopping panic requires speaking range or comms")
    if method in ("repair", "surgery"):
        if state["mode"] == "combat" or intent.get("shift_completed") is not True:
            raise LoreKitError("Repair/surgery requires a completed shift outside combat")
    if method == "repair" and target["species"] != "synthetic":
        raise LoreKitError("Repair target must be synthetic")
    if target.get("shutdown") and method == "repair":
        raise LoreKitError("Shutdown synthetic can only be reactivated for communication")
    if method == "surgery":
        selected = intent.get("injury")
        if not any(i["id"] == selected and i["surgery_required"] for i in target["injuries"]):
            raise LoreKitError("Select an injury requiring surgery")
    if not self_rally:
        scenes.spend(state, helper_id, "quick" if method == "rally" else "full")
    skill = "command" if method in ("rally", "stop_panic") else "comtech" if method == "repair" else "medical_aid"
    if self_rally:
        skill = "stamina"
    modifier = -2 if helper_id == target_id and method == "first_aid" else 0
    return checks.start(
        state,
        {
            "actor": helper_id,
            "skill": skill,
            "modifier": modifier,
            "context": method,
            "no_push": self_rally,
            "gear": intent.get("gear", []),
            "continuation": {"kind": "recover", "method": method, "target": target_id, "injury": intent.get("injury")},
        },
        bundle,
        rng,
    )


def finish_recover(state, result, bundle, rng):
    continuation = result["continuation"]
    target = actor(state, continuation["target"])
    method = continuation["method"]
    if result["success"]:
        if method in ("first_aid", "rally"):
            if target["health"] == 0 and not target["fatigued"] and not target.get("hotspot"):
                restore(target, result["successes"][0])
            if method == "first_aid":
                for injury in target["injuries"]:
                    if injury["lethal"]:
                        injury["stabilized"] = True
        elif method == "repair":
            target["health"] = target["health_max"]
            target["system_damage"] = []
            target.pop("skip_turn", None)
            target.pop("interrupt_blocked", None)
        elif method == "surgery":
            next(i for i in target["injuries"] if i["id"] == continuation["injury"])["surgery_required"] = False
        elif method == "stop_panic":
            effects.stop_panic(target)
    elif method == "first_aid":
        for injury in target["injuries"]:
            if not injury["lethal"] or injury["stabilized"]:
                continue
            if injury["interval"] == "round":
                target["dead"] = True
            else:
                injury["interval"] = {"shift": "stretch", "stretch": "round"}[injury["interval"]]
                injury["due_unit"] = "turn" if injury["interval"] == "round" and state["combat"] else injury["interval"]
                injury["due"] = (
                    target.get("turns_completed", 0)
                    if injury["due_unit"] == "turn"
                    else state["clocks"][injury["interval"]]
                ) + 1
    return {
        "type": "recovery_complete",
        "method": method,
        "target": continuation["target"],
        "success": result["success"],
        "checks": result["checks"],
    }


def stun(state, target_id, amount, bundle, rng):
    from lorekit.alien import checks

    a = actor(state, target_id)
    if a["species"] == "xenomorph":
        return {"type": "stun_immune", "actor": target_id}
    return checks.start(
        state,
        {
            "actor": target_id,
            "skill": "stamina",
            "modifier": -max(0, amount - a["armor"]),
            "continuation": {"kind": "stun", "actor": target_id},
        },
        bundle,
        rng,
        queued=True,
    )


def finish_stun(state, result):
    a = actor(state, result["continuation"]["actor"])
    a["stunned"] = not result["success"]
    return {
        "type": "stun_result",
        "actor": result["continuation"]["actor"],
        "stunned": a["stunned"],
        "checks": result["checks"],
    }


def finish_exposure(state, result):
    data = result["continuation"]
    a = actor(state, data["actor"])
    if not result["success"]:
        if not any(h["id"] == "disease" for h in a["hazards"]):
            a["hazards"].append(
                {
                    "id": "disease",
                    "kind": "disease",
                    "unit": data["unit"],
                    "penalty": data["penalty"],
                    "due": state["clocks"][data["unit"]] + 1,
                }
            )
        a["fatigued"] = True
    return {"type": "disease_exposure", "infected": not result["success"], "checks": result["checks"]}


def remedy(state, intent):
    a = actor(state, intent.get("actor"))
    if not isinstance(intent.get("reason"), str) or not intent["reason"].strip():
        raise LoreKitError("Remedy requires a concrete GM reason")
    selected = next((h for h in a.get("hazards", []) if h["id"] == intent.get("hazard")), None)
    if selected is None:
        raise LoreKitError("Hazard not active")
    a["hazards"].remove(selected)
    a["fatigued"] = any(
        h["kind"] in ("air", "hunger", "cold", "heat", "sleep", "disease", "hypersleep") for h in a["hazards"]
    )
    return {"type": "hazard_remedied", "actor": intent["actor"], "hazard": selected["id"], "reason": intent["reason"]}


def sync_air(state, aid):
    a = actor(state, aid)
    if a["species"] != "human":
        return
    active = [i for i in a["inventory"] if i.get("active_air") and i.get("equipped") and not i.get("dropped")]
    if active and all(i["supply"] == 0 for i in active):
        if not any(h["id"] == "air" for h in a.setdefault("hazards", [])):
            a["hazards"].append(
                {"id": "air", "kind": "air", "unit": "round", "penalty": 0, "due": state["clocks"]["round"] + 1}
            )


def injury_decide(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] not in ("lucky_injury", "lucky_death") or p["actor"] != intent.get("actor"):
        raise LoreKitError("No matching Lucky decision")
    choice = integer(intent.get("choice"), "Lucky result index", 0, 1)
    state["pending"] = None
    if p["type"] == "lucky_injury":
        result = apply_injury(state, p["actor"], bundle, rng, p["values"][choice], p["dice"][choice])
    else:
        result = finish_due(state, {**p["results"][choice], "lucky_chosen": True}, bundle, rng)
    result["events"] = result.get("events", []) + effects.drain(state, rng, bundle=bundle)
    return result


def resist(a, amount, bundle, rng):
    dice = []
    if amount and any(e["hook"] == "resilient" for e in talents(a, bundle)):
        dice = [rng.randint(1, 6) for _ in range(a["attributes"]["strength"])]
        amount = max(0, amount - dice.count(6))
    return amount, dice
