"""Stealth sequence, passive detection and tracker pings on zone maps."""

from __future__ import annotations

import copy

from lorekit.alien import checks, scenes, survival
from lorekit.alien.characters import bonus, incapacitated
from lorekit.alien.state import actor
from lorekit.db import LoreKitError


def observation(state, aid, bundle, rng, *, active=False, modifier=0):
    a = actor(state, aid)
    zone = state["scene"]["zones"][a["zone"]]
    if zone.get("dark") and a["species"] != "xenomorph":
        modifier -= 2
    if a.get("guarding") and not active:
        modifier += 2
    return checks.roll(
        state, {"actor": aid, "skill": "observation", "modifier": modifier, "passive": not active}, bundle, rng
    )


def detect(state, first, second, bundle, rng, before=None):
    # Highest roll represents each group; each participant rolls individually.
    groups = (first, second)
    rolls = []
    for index, group in enumerate(groups):
        opposing = groups[1 - index]
        stealth_penalty = min(
            [
                bundle["collections"]
                .get("creatures", {})
                .get(actor(state, bid).get("content_id"), {})
                .get("stealth_penalty", 0)
                + bonus(actor(state, bid), bundle, "stealth_penalty")
                for bid in opposing
            ]
            or [0]
        )
        group_rolls = []
        for aid in group:
            a = actor(state, aid)
            warmth = (
                a.get("stage") == "facehugger"
                and state["scene"]["zones"][a["zone"]].get("body_temperature")
                and any(actor(state, bid)["species"] == "human" for bid in opposing)
            )
            group_rolls.append(observation(state, aid, bundle, rng, modifier=-stealth_penalty - (2 if warmth else 0)))
        rolls.append(group_rolls)
    values = [max(checks.successes(c) for c in group) for group in rolls]
    if values[0] == values[1]:
        return scenes.enter_combat(state, {"actors": first + second}, bundle, rng)
    winner = first if values[0] > values[1] else second
    state["pending"] = {
        "type": "detection",
        "id": f"detection:{state['revision'] + 1}",
        "winner": winner,
        "first": first,
        "second": second,
        "before": before or {},
    }
    return {"type": "detection", "checks": rolls, "pending": copy.deepcopy(state["pending"])}


def act(state, intent, bundle, rng):
    if state["mode"] not in ("stealth", "combat") or not state["scene"]:
        raise LoreKitError("Stealth actions require an active stealth scene")
    aid = intent.get("actor")
    a = actor(state, aid)
    group = intent.get("group", [aid])
    if not isinstance(group, list) or not group or aid not in group or len(set(group)) != len(group):
        raise LoreKitError("Invalid stealth group")
    scene = state["scene"]
    for member in group:
        candidate = actor(state, member)
        if state["combat"] and member in state["combat"]["actors"] and not candidate.get("escaped"):
            raise LoreKitError("Active combatant cannot take a stealth turn")
        expected = "pc" if candidate["kind"] == "pc" else "npc"
        if scene["phase"] != expected or member in scene["acted"] or candidate["dead"] or incapacitated(candidate):
            raise LoreKitError("Actor cannot act in this stealth phase")
    action = intent.get("action")
    was_hidden = all(actor(state, member)["hidden"] for member in group)
    events = []
    before = {member: actor(state, member)["zone"] for member in group}
    if action == "move" and "path" in intent:
        path = intent["path"]
        maximum = a.get("speed", 1) if a["species"] == "xenomorph" else 1
        if len(group) != 1 or not isinstance(path, list) or not 1 <= len(path) <= maximum:
            raise LoreKitError("Declare one NPC's path within its Speed movement limit")
        for destination in path:
            before = {aid: a["zone"]}
            events.append(scenes.move(state, aid, destination, bundle, rng=rng))
            enemies = [
                bid
                for bid, other in sorted(state["actors"].items())
                if other["kind"] != a["kind"] and not other["dead"] and scenes.line_of_sight(state, aid, bid)
            ]
            if enemies:
                scene["acted"].append(aid)
                result = detect(state, group, enemies, bundle, rng, before)
                result["events"] = events
                return result
    elif action == "move":
        destination = intent.get("zone")
        for member in group:
            events.append(scenes.move(state, member, destination, bundle, rng=rng))
    elif action == "hide":
        for member in group:
            if not scene["zones"][actor(state, member)["zone"]].get("cluttered"):
                raise LoreKitError("Cannot hide in an open zone")
            actor(state, member)["hidden"] = True
    elif action == "guard":
        for member in group:
            actor(state, member)["guarding"] = True
    elif action == "search":
        # Active checks may be pushed; the continuation reveals one eligible
        # hidden fact per success after decisions are complete.
        if len(group) != 1:
            raise LoreKitError("Search separately or declare helpers in a check")
        spec = {
            "actor": aid,
            "skill": "observation",
            "context": "search",
            "modifier": -2 if scene["zones"][a["zone"]].get("dark") and a["species"] != "xenomorph" else 0,
            "continuation": {"kind": "search", "actor": aid},
        }
        scene["acted"].append(aid)
        return checks.start(state, spec, bundle, rng)
    elif action in ("interact", "ambush"):
        if not was_hidden or not isinstance(intent.get("description"), str) or not intent["description"].strip():
            raise LoreKitError("Hidden actions need hidden actors and an explicit description")
        events.append({"type": "hidden_action", "description": intent["description"]})
    elif action == "wait":
        pass
    else:
        raise LoreKitError("Unsupported stealth action")
    for member in group:
        if action != "guard":
            actor(state, member).pop("guarding", None)
        scene["acted"].append(member)
    # Same-kind groups are treated as friendlies unless GM declares otherwise.
    enemies = [
        bid
        for bid, b in state["actors"].items()
        if bid not in group
        and b["kind"] != a["kind"]
        and not b["dead"]
        and any(scenes.line_of_sight(state, member, bid) for member in group)
    ]
    if enemies and not (was_hidden and action in ("hide", "wait", "guard")):
        result = detect(state, group, enemies, bundle, rng, before)
        result["events"] = events
        return result
    return {"type": "stealth_action", "events": events}


def phase(state, rng, bundle, *, elapsed=False):
    scene = state["scene"]
    if not scene or state["mode"] not in ("stealth", "combat"):
        raise LoreKitError("No active stealth sequence")
    required = [
        aid
        for aid, a in state["actors"].items()
        if not a["dead"]
        and not incapacitated(a)
        and a["zone"] is not None
        and ("pc" if a["kind"] == "pc" else "npc") == scene["phase"]
        and (not state["combat"] or aid not in state["combat"]["actors"] or a.get("escaped"))
    ]
    if any(aid not in scene["acted"] for aid in required):
        raise LoreKitError("All actors in this phase must act or wait")
    if scene["phase"] == "pc":
        scene["phase"] = "npc"
    else:
        if state["combat"] and not elapsed:
            raise LoreKitError("Confirm elapsed:true to advance a full stealth stretch while combat continues")
        scene["phase"] = "pc"
        scene["acted"] = []
        scene["previous_moved"] = scene["moved"]
        scene["moved"] = []
        for a in state["actors"].values():
            a.pop("moved_this_stretch", None)
        survival.advance_time(state, "stretch", 1)
    return {"type": "stealth_phase", "phase": scene["phase"]}


def decide(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "detection" or p["id"] != intent.get("decision_id"):
        raise LoreKitError("No matching detection choice")
    if intent.get("actor") not in p["winner"]:
        raise LoreKitError("Only the winning side chooses its detection response")
    choice = intent.get("choice")
    if choice == "hide":
        for aid in p["winner"]:
            a = actor(state, aid)
            if not state["scene"]["zones"][a["zone"]].get("cluttered"):
                raise LoreKitError("Cannot hide in an open zone")
            a["hidden"] = True
    elif choice == "back_out":
        for aid in p["winner"]:
            if aid not in p["before"]:
                raise LoreKitError("Winning side has no recorded entry route; GM ruling required")
            scenes.move(state, aid, p["before"][aid], bundle, forced=True, rng=rng)
    elif choice in ("reveal", "ambush"):
        for aid in p["first"] + p["second"]:
            actor(state, aid)["revealed"] = True
        state["pending"] = None
        return scenes.enter_combat(
            state,
            {
                "actors": p["first"] + p["second"],
                "surprise": p["winner"] if choice == "ambush" else [],
                "ambush": p["winner"] if choice == "ambush" else [],
            },
            bundle,
            rng,
        )
    else:
        raise LoreKitError("Choose reveal, ambush, hide or back_out")
    state["pending"] = None
    return {"type": "detection_resolved", "choice": choice}


def tracker(state, intent, bundle, rng):
    if state["mode"] != "stealth":
        raise LoreKitError("Motion tracker use requires stealth mode")
    a = actor(state, intent.get("actor"))
    if a.get("tracker_stretch") == state["clocks"]["stretch"]:
        raise LoreKitError("Tracker already used this stretch")
    instance, definition = survival.item(a, intent.get("item"), bundle)
    if definition.get("tracker_range") is None:
        raise LoreKitError("Item is not a motion tracker")
    rolled = survival.supply(a, instance["id"], bundle, rng)
    a["tracker_stretch"] = state["clocks"]["stretch"]
    pings = []
    if rolled["remaining"]:
        for aid in state["scene"]["previous_moved"]:
            other = actor(state, aid)
            if (
                other["kind"] == "npc"
                and scenes.distance(state, a["zone"], other["zone"]) <= definition["tracker_range"]
            ):
                pings.append(other["zone"])
    return {"type": "tracker", "supply": rolled, "pings": sorted(pings)}


def search_finish(state, result):
    aid = result["continuation"]["actor"]
    a = actor(state, aid)
    candidates = [
        "actor:" + bid
        for bid, b in sorted(state["actors"].items())
        if bid != aid and b["zone"] == a["zone"] and b["hidden"]
    ]
    candidates += [
        "object:" + key
        for key, obj in sorted(state["scene"].get("objects", {}).items())
        if obj["zone"] == a["zone"] and not obj.get("found")
    ]
    count = min(result["successes"][0], len(candidates))
    if count:
        state["pending"] = {"type": "search_results", "actor": aid, "count": count, "candidates": candidates}
    return {"type": "search_complete", "checks": result["checks"], "pending": copy.deepcopy(state["pending"])}


def search_decide(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "search_results" or intent.get("actor") != p["actor"]:
        raise LoreKitError("No matching search results decision")
    chosen = intent.get("found")
    if (
        not isinstance(chosen, list)
        or len(chosen) != p["count"]
        or len(set(chosen)) != len(chosen)
        or any(key not in p["candidates"] for key in chosen)
    ):
        raise LoreKitError("GM must choose one hidden discovery per success, up to the eligible count")
    revealed = []
    for key in chosen:
        family, identifier = key.split(":", 1)
        if family == "actor":
            a = actor(state, identifier)
            a.update(hidden=False, revealed=True)
            revealed.append(identifier)
        else:
            state["scene"]["objects"][identifier]["found"] = True
    aid = p["actor"]
    state["pending"] = None
    enemies = [tid for tid in revealed if actor(state, tid)["kind"] != actor(state, aid)["kind"]]
    event = {"type": "search_discoveries", "actor": aid, "found": chosen}
    if enemies:
        event["combat"] = scenes.enter_combat(state, {"actors": [aid] + enemies}, bundle, rng)
    return event


def leave(state, intent, bundle, rng):
    aid = intent.get("actor")
    a = actor(state, aid)
    if not state["combat"] or aid not in state["combat"]["actors"] or a.get("escaped"):
        raise LoreKitError("Actor is not leaving an active combat")
    if intent.get("route_unclear") is not True:
        raise LoreKitError("GM must confirm the route taken is not obvious")
    enemies = [
        tid
        for tid in state["combat"]["actors"]
        if tid != aid
        and actor(state, tid).get("side", actor(state, tid)["kind"]) != a.get("side", a["kind"])
        and not actor(state, tid)["dead"]
        and not actor(state, tid).get("escaped")
    ]
    if any(scenes.line_of_sight(state, tid, aid) for tid in enemies):
        raise LoreKitError("Move out of all opponents' line of sight before leaving combat")
    rolls = [observation(state, tid, bundle, rng) for tid in enemies]
    escaped = not any(checks.successes(roll) for roll in rolls)
    if escaped:
        a.update(escaped=True, hidden=True)
        if not any(
            other["kind"] == "pc" and not other["dead"] and not other.get("escaped")
            for other in [actor(state, tid) for tid in state["combat"]["actors"]]
        ):
            scenes.finish_combat(state)
    return {"type": "left_combat", "actor": aid, "escaped": escaped, "checks": rolls}
