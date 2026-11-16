"""Stealth sequence, passive detection and tracker pings on zone maps."""

from __future__ import annotations

import copy

from lorekit.alien import checks, scenes, survival
from lorekit.alien.characters import bonus
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
        rolls.append([observation(state, aid, bundle, rng, modifier=-stealth_penalty) for aid in group])
    values = [max(checks.successes(c) for c in group) for group in rolls]
    if values[0] == values[1]:
        return scenes.start_combat(state, {"actors": first + second}, rng, bundle)
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
    if state["mode"] != "stealth" or not state["scene"]:
        raise LoreKitError("Stealth actions require an active stealth scene")
    aid = intent.get("actor")
    a = actor(state, aid)
    group = intent.get("group", [aid])
    if not isinstance(group, list) or not group or aid not in group or len(set(group)) != len(group):
        raise LoreKitError("Invalid stealth group")
    scene = state["scene"]
    for member in group:
        candidate = actor(state, member)
        expected = "pc" if candidate["kind"] == "pc" else "npc"
        if scene["phase"] != expected or member in scene["acted"] or candidate["dead"] or candidate["health"] == 0:
            raise LoreKitError("Actor cannot act in this stealth phase")
    action = intent.get("action")
    events = []
    before = {member: actor(state, member)["zone"] for member in group}
    if action == "move":
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
            "continuation": {"kind": "search", "actor": aid},
        }
        scene["acted"].append(aid)
        return checks.start(state, spec, bundle, rng)
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
    if enemies:
        result = detect(state, group, enemies, bundle, rng, before)
        result["events"] = events
        return result
    return {"type": "stealth_action", "events": events}


def phase(state, rng, bundle):
    scene = state["scene"]
    if not scene or state["mode"] != "stealth":
        raise LoreKitError("No active stealth sequence")
    required = [
        aid
        for aid, a in state["actors"].items()
        if not a["dead"]
        and a["health"] > 0
        and a["zone"] is not None
        and ("pc" if a["kind"] == "pc" else "npc") == scene["phase"]
    ]
    if any(aid not in scene["acted"] for aid in required):
        raise LoreKitError("All actors in this phase must act or wait")
    if scene["phase"] == "pc":
        scene["phase"] = "npc"
    else:
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
        return scenes.start_combat(
            state,
            {"actors": p["first"] + p["second"], "surprise": p["winner"] if choice == "ambush" else []},
            rng,
            bundle,
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
