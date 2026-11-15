"""Explicit GM/actor choices for effects which cannot choose their own fiction."""

from __future__ import annotations

import copy

from lorekit.alien import checks, combat, effects, scenes, survival
from lorekit.alien.state import actor
from lorekit.db import LoreKitError


def panic(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "panic_choice" or intent.get("actor") != p["actor"]:
        raise LoreKitError("No matching panic choice")
    a = actor(state, p["actor"])
    result = p["result"]
    event = {"type": "panic_choice", "actor": p["actor"], "result": result}
    if result == 2:
        alerted = intent.get("alerted")
        if not isinstance(alerted, list) or any(not isinstance(aid, str) for aid in alerted):
            raise LoreKitError("GM must identify nearby enemies to alert")
        for aid in alerted:
            actor(state, aid)["alerted"] = True
    elif result == 3:
        event["supply"] = survival.supply(a, intent.get("item"), bundle, rng)
    elif result == 4:
        entry, _ = survival.item(a, intent.get("item"), bundle)
        entry["dropped"] = True
        entry["equipped"] = False
        entry["ground_zone"] = a["zone"]
    elif result == 10:
        old_friends = effects.friendly(state, p["actor"])
        options = [
            b["b"] if b["a"] == a["zone"] else b["a"]
            for b in state["scene"]["borders"]
            if a["zone"] in (b["a"], b["b"]) and scenes.traversable(b)
        ]
        if not options:
            a["panic_responses"] = [r for r in a["panic_responses"] if r != 10]
            a["panic_responses"].append(12)
        else:
            if intent.get("zone") not in options or intent.get("away_from_threat") is not True:
                raise LoreKitError("Choose a reachable adjacent zone away from the threat")
            scenes.move(state, p["actor"], intent["zone"], bundle, forced=True)
            a["stress"] = max(0, a["stress"] - 1)
            for aid in old_friends:
                effects.stress(actor(state, aid), 1)
    elif result == 11:
        target_id = intent.get("target")
        target = actor(state, target_id)
        candidates = [
            aid
            for aid, other in state["actors"].items()
            if aid != p["actor"] and not other["dead"] and other["zone"] is not None
        ]
        if not candidates:
            raise LoreKitError("No target; explicit GM ruling required")
        minimum = min(scenes.range_band(state, p["actor"], aid) for aid in candidates)
        if scenes.range_band(state, p["actor"], target_id) != minimum:
            raise LoreKitError("Frenzy must target a nearest character or creature")
        state["pending"] = None
        return combat.declare(
            state, {"actor": p["actor"], "target": target_id, "weapon": intent.get("weapon")}, bundle, forced=True
        )
    else:
        raise LoreKitError("Unsupported panic decision")
    state["pending"] = None
    event["events"] = effects.drain(state, rng, bundle=bundle)
    event["pending"] = copy.deepcopy(state["pending"])
    return event


def burst(state, intent, bundle):
    p = state["pending"]
    if not p or p["type"] != "burst" or p["attack"]["actor"] != intent.get("actor"):
        raise LoreKitError("No matching full-auto continuation")
    attack = copy.deepcopy(p["attack"])
    if intent.get("choice") == "stop":
        state["pending"] = None
        return {"type": "burst_stopped"}
    if intent.get("choice") != "continue":
        raise LoreKitError("Choose continue or stop")
    a = actor(state, attack["actor"])
    instance, definition = survival.item(a, attack["weapon"], bundle)
    if not instance["supply"] or instance.get("jammed"):
        raise LoreKitError("Cannot continue full auto with an empty or jammed weapon")
    effects.stress(a, 1)
    attack["burst"] += 1
    if attack["aim"] and attack["burst"] == 2:
        attack["modifier"] -= 2
    tid = intent.get("target", attack["target"])
    if actor(state, tid)["dead"]:
        raise LoreKitError("Choose a living burst target")
    if tid != attack["target"]:
        if (
            not scenes.line_of_sight(state, attack["actor"], tid)
            or scenes.range_band(state, attack["actor"], tid) != attack["band"]
        ):
            raise LoreKitError("Retargeting across range/cover modifiers requires an explicit ruling")
        if actor(state, tid).get("cover") != actor(state, attack["target"]).get("cover"):
            raise LoreKitError("Retargeting with different cover requires an explicit ruling")
    attack["target"] = tid
    state["pending"] = {"type": "defense", "id": f"defense:{state['revision'] + 1}", "attack": attack}
    return {"type": "defense_required", "decision_id": state["pending"]["id"], "target": tid}
