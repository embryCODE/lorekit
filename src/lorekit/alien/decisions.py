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
            if p.get("subsequent_turn") and intent.get("safe") is True:
                a["safe_flee_zone"] = a["zone"]
                state["pending"] = None
                return {"type": "panic_flee_waiting", "actor": p["actor"]}
            path = intent.get("path", [intent.get("zone")])
            remaining = 1
            if p.get("subsequent_turn"):
                budget = state["combat"]["budgets"][p["actor"]]
                remaining = (1 if budget["full"] else 2) - budget["quick"]
            if remaining == 0:
                state["pending"] = None
                return {"type": "panic_flee_exhausted", "actor": p["actor"]}
            if (
                not isinstance(path, list)
                or len(path) != remaining
                or not path
                or intent.get("away_from_threat") is not True
            ):
                raise LoreKitError("Flee along a path using all remaining moves, confirmed away from the threat")
            for zone in path:
                scenes.move(state, p["actor"], zone, bundle, forced=True, rng=rng)
            if p.get("subsequent_turn"):
                state["combat"]["budgets"][p["actor"]] = {"full": True, "quick": 1}
            else:
                a["stress"] = max(0, a["stress"] - 1)
                for aid in old_friends:
                    effects.stress(actor(state, aid), 1)
    elif result == 11:
        target_id = a.get("frenzy_target", intent.get("target"))
        if intent.get("target", target_id) != target_id:
            raise LoreKitError("Frenzy continues against its original target")
        target = actor(state, target_id)
        if target["health"] == 0 or target["dead"]:
            effects.stop_panic(a)
            state["pending"] = None
            return {"type": "frenzy_stopped", "actor": p["actor"]}
        candidates = [
            aid
            for aid, other in state["actors"].items()
            if aid != p["actor"] and not other["dead"] and other["zone"] is not None
        ]
        if not candidates:
            raise LoreKitError("No target; explicit GM ruling required")
        minimum = min(scenes.range_band(state, p["actor"], aid) for aid in candidates)
        if not a.get("frenzy_target") and scenes.range_band(state, p["actor"], target_id) != minimum:
            raise LoreKitError("Frenzy must target a nearest character or creature")
        a["frenzy_target"] = target_id
        if p.get("subsequent_turn"):
            # Forced attacks on later turns still spend the ordinary budget.
            a["panic_responses"].remove(11)
            scenes.spend(state, p["actor"], "full")
            a["panic_responses"].append(11)
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
    from lorekit.alien.characters import talents

    if not any(e["hook"] == "machinegunner" for e in talents(a, bundle)):
        effects.stress(a, 1)
    tid = intent.get("target", attack["target"])
    state["pending"] = None
    result = combat.declare(
        state,
        {
            "actor": attack["actor"],
            "target": tid,
            "weapon": attack["weapon"],
            "full_auto": True,
            "weak_spot": attack["weak_spot"],
            "known_target": intent.get("known_target", attack.get("known_target", False)),
        },
        bundle,
        forced=True,
    )
    state["pending"]["attack"]["burst"] = attack["burst"] + 1
    return result


def overkill(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "overkill_choice" or intent.get("actor") != p["actor"]:
        raise LoreKitError("No matching Overkill decision")
    a = actor(state, p["actor"])
    state["pending"] = None
    if intent.get("choice") == "overkill":
        enemies = intent.get("enemies")
        if not isinstance(enemies, list) or not enemies or len(set(enemies)) != len(enemies) or p["actor"] in enemies:
            raise LoreKitError("Declare the enemies who must be attacked")
        for enemy in enemies:
            actor(state, enemy)
        a["overkill"] = enemies
        for tid, witness in sorted(state["actors"].items()):
            if tid != p["actor"] and witness["zone"] == a["zone"] and effects.eligible(witness):
                effects.enqueue_panic(state, tid, "witnessed_overkill")
        event = {"type": "overkill", "actor": p["actor"], "enemies": enemies}
    elif intent.get("choice") == "panic":
        event = effects.panic(state, p["actor"], rng, bundle=bundle, rolled=p["die"])
    else:
        raise LoreKitError("Choose overkill or panic")
    event["events"] = effects.drain(state, rng, bundle=bundle)
    return event
