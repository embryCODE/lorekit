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
        survival.sync_air(state, p["actor"])
    elif result == 4:
        entry, _ = survival.item(a, intent.get("item"), bundle)
        entry["dropped"] = True
        entry["equipped"] = False
        entry["ground_zone"] = a["zone"]
        entry["lost"] = state["mode"] != "combat"
    elif result == 10:
        old_friends = effects.friendly(state, p["actor"])
        options = []
        for border in state["scene"]["borders"]:
            if a["zone"] not in (border["a"], border["b"]):
                continue
            destination = border["b"] if border["a"] == a["zone"] else border["a"]
            try:
                scenes.route(state, p["actor"], destination)
            except LoreKitError:
                continue
            options.append(destination)
        if (
            a.get("immobilized_by")
            and not actor(state, a["immobilized_by"])["dead"]
            or any(e["hook"] == "immobile" for i in a["injuries"] for e in i["effects"])
        ):
            options = []
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
                remaining = (
                    int(not budget["full"] and budget["quick"] < 2)
                    if scenes.movement_cost(a) == "full"
                    else (1 if budget["full"] else 2) - budget["quick"]
                )
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
            from lorekit.alien import forced_movement
            from lorekit.alien.engine import complete_check

            state["pending"] = None
            plan = {
                "kind": "flee",
                "actor": p["actor"],
                "path": path,
                "index": 0,
                "paid": bool(p.get("subsequent_turn")),
                "friends": old_friends,
            }
            return complete_check(state, forced_movement.run(state, plan, bundle, rng), bundle, rng)
    elif result == 11:
        target_id = a.get("frenzy_target", intent.get("target"))
        if intent.get("target", target_id) != target_id:
            raise LoreKitError("Frenzy continues against its original target")
        target = actor(state, target_id)
        from lorekit.alien.characters import incapacitated

        if incapacitated(target):
            a["panic_responses"] = [value for value in a["panic_responses"] if value != 11]
            a.pop("frenzy_target", None)
            if not any(value >= 7 for value in a["panic_responses"]):
                a.pop("interrupt_blocked", None)
            state["pending"] = None
            return {"type": "frenzy_stopped", "actor": p["actor"]}
        candidates = [
            aid
            for aid, other in state["actors"].items()
            if aid != p["actor"]
            and not other["dead"]
            and other["zone"] is not None
            and scenes.line_of_sight(state, p["actor"], aid)
        ]
        if not a.get("frenzy_target"):
            if not candidates:
                raise LoreKitError("No target; explicit GM ruling required")
            minimum = min(scenes.range_band(state, p["actor"], aid) for aid in candidates)
            if scenes.range_band(state, p["actor"], target_id) != minimum:
                raise LoreKitError("Frenzy must target a nearest character or creature")
        a["frenzy_target"] = target_id
        if state["combat"] is None:
            state["pending"] = None
            scenes.start_combat(
                state, {"actors": [p["actor"]] + list(dict.fromkeys(candidates + [target_id]))}, rng, bundle
            )
            if state["pending"]:
                state["queue"].append({"type": "panic_resume", "decision": copy.deepcopy(p)})
                return {"type": "frenzy_started_combat", "pending": copy.deepcopy(state["pending"])}
        from lorekit.alien import forced_movement
        from lorekit.alien.engine import complete_check

        path = intent.get("path", [])
        if not isinstance(path, list) or len(path) > 2 or type(intent.get("approach", False)) is not bool:
            raise LoreKitError("Declare a legal frenzy approach path and optional approach:true")
        state["pending"] = None
        plan = {
            "kind": "frenzy",
            "actor": p["actor"],
            "path": path,
            "index": 0,
            "paid": True,
            "target": target_id,
            "weapon": intent.get("weapon"),
            "subsequent": bool(p.get("subsequent_turn")),
            "approach": intent.get("approach", False),
        }
        return complete_check(state, forced_movement.run(state, plan, bundle, rng), bundle, rng)
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
