"""Zone topology, initiative slots and action accounting for Alien Evolved."""

from __future__ import annotations

import copy
from collections import deque

from lorekit.alien import effects
from lorekit.alien.characters import load_weight
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def setup(state, spec):
    zones = spec.get("zones")
    borders = spec.get("borders", [])
    if not isinstance(zones, dict) or not zones or any(not isinstance(k, str) or not k for k in zones):
        raise LoreKitError("Scene requires named zone objects")
    for zone in zones.values():
        if not isinstance(zone, dict) or set(zone) - {"cluttered", "dark", "crawlspace", "zero_g", "flammable"}:
            raise LoreKitError("Unsupported zone property")
        if any(type(v) is not bool for v in zone.values()):
            raise LoreKitError("Zone flags must be boolean")
    if not isinstance(borders, list):
        raise LoreKitError("borders must be a list")
    seen = set()
    for b in borders:
        if not isinstance(b, dict) or set(b) - {"a", "b", "open", "door", "locked", "sealed", "peek"}:
            raise LoreKitError("Invalid scene border")
        if b.get("a") not in zones or b.get("b") not in zones or b["a"] == b["b"]:
            raise LoreKitError("Invalid border endpoints")
        pair = tuple(sorted((b["a"], b["b"])))
        if pair in seen:
            raise LoreKitError("Duplicate border")
        seen.add(pair)
        if any(type(b[k]) is not bool for k in set(b) - {"a", "b"}):
            raise LoreKitError("Border flags must be boolean")
    for aid, zid in spec.get("placements", {}).items():
        a = actor(state, aid)
        if zid not in zones:
            raise LoreKitError("Unknown placement zone")
        a["zone"] = zid
        a["adjacent"] = []
    state["scene"] = {
        "zones": copy.deepcopy(zones),
        "borders": copy.deepcopy(borders),
        "phase": "pc",
        "acted": [],
        "moved": [],
        "previous_moved": [],
    }
    state["mode"] = "stealth"
    return {"type": "scene_setup"}


def border(state, start, end):
    if not state["scene"]:
        raise LoreKitError("Scene is not configured")
    return next((b for b in state["scene"]["borders"] if {b["a"], b["b"]} == {start, end}), None)


def traversable(b):
    return (
        b
        and not b.get("locked", False)
        and not b.get("sealed", False)
        and (b.get("open", False) or b.get("door", False))
    )


def distance(state, start, end):
    if start == end and start is not None:
        return 0
    if not state["scene"] or start is None or end is None:
        raise LoreKitError("Actors need scene positions")
    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        zone, d = queue.popleft()
        for b in state["scene"]["borders"]:
            if zone not in (b["a"], b["b"]):
                continue
            other = b["b"] if b["a"] == zone else b["a"]
            if other == end:
                return d + 1
            if other not in seen:
                seen.add(other)
                queue.append((other, d + 1))
    raise LoreKitError("No mapped range between these zones")


def line_of_sight(state, aid, bid):
    a, b = actor(state, aid), actor(state, bid)
    if a["zone"] is None or b["zone"] is None or b["hidden"] or b.get("cover") == "full":
        return False
    if a["zone"] == b["zone"]:
        return True
    edge = border(state, a["zone"], b["zone"])
    return bool(edge and (edge.get("open") or edge.get("peek")) and not edge.get("sealed"))


def range_band(state, aid, bid):
    a, b = actor(state, aid), actor(state, bid)
    d = distance(state, a["zone"], b["zone"])
    if d == 0:
        return 0 if bid in a.get("adjacent", []) else 1
    return 2 if d == 1 else 3 if d <= 4 else 4


def start_combat(state, intent, rng):
    if state["combat"] is not None:
        raise LoreKitError("Combat already active")
    participants = intent.get("actors")
    if not isinstance(participants, list) or not participants or len(set(participants)) != len(participants):
        raise LoreKitError("Combat requires distinct actor IDs")
    for aid in participants:
        a = actor(state, aid)
        if a["dead"] or a["zone"] is None:
            raise LoreKitError("Combat actor is dead or unplaced")
    groups = intent.get("groups", [])
    if not isinstance(groups, list) or any(not isinstance(g, list) or not g for g in groups):
        raise LoreKitError("groups must be nonempty lists of NPC IDs")
    flattened = [x for group in groups for x in group]
    if len(set(flattened)) != len(flattened) or any(
        x not in participants or actor(state, x)["kind"] != "npc" for x in flattened
    ):
        raise LoreKitError("Initiative groups require distinct participating NPCs")
    units = [list(g) for g in groups] + [[aid] for aid in participants if aid not in flattened]
    state["combat"] = {"actors": participants[:], "units": units, "round": 0, "slots": [], "index": 0, "budgets": {}}
    state["mode"] = "combat"
    surprise = intent.get("surprise", [])
    for aid in surprise:
        actor(state, aid)["ambush_targets"] = [tid for tid in participants if tid not in surprise]
    draw_round(state, rng, surprise)
    return {"type": "combat_started", "combat": copy.deepcopy(state["combat"])}


def draw_round(state, rng, surprise=None):
    combat = state["combat"]
    expanded = []
    for unit in combat["units"]:
        speed = actor(state, unit[0]).get("speed", 1)
        for _ in range(speed):
            expanded.append(unit)
    if len(expanded) > 10:
        raise LoreKitError("More than ten initiative draws: group NPCs or record a ruling")
    if combat["round"] > 0 and state["options"]["fixed_initiative"]:
        combat["index"] = 0
        combat["round"] += 1
        combat["budgets"] = {aid: {"full": False, "quick": 0} for aid in combat["actors"]}
        for aid in combat["actors"]:
            actor(state, aid).pop("moved_this_round", None)
        return
    deck = list(range(1, 11))
    rng.shuffle(deck)
    slots = []
    surprise = surprise or []
    if any(aid not in combat["actors"] for aid in surprise):
        raise LoreKitError("Surprise actor is not a combat participant")
    expanded.sort(key=lambda unit: not any(aid in surprise for aid in unit))
    surprised = set()
    hesitant = [u for u in expanded if any(6 in actor(state, aid)["panic_responses"] for aid in u)]
    rng.shuffle(hesitant)
    for unit in hesitant:
        card = max(deck)
        deck.remove(card)
        slots.append({"card": card, "actors": unit[:]})
    for unit in expanded:
        if unit in hesitant:
            continue
        if any(aid in surprise and aid not in surprised for aid in unit):
            card = min(deck)
            surprised.update(unit)
        else:
            card = deck[0]
        deck.remove(card)
        slots.append({"card": card, "actors": unit[:]})
    combat["slots"] = sorted(slots, key=lambda s: s["card"])
    combat["index"] = 0
    combat["round"] += 1
    combat["budgets"] = {aid: {"full": False, "quick": 0} for aid in combat["actors"]}
    for aid in combat["actors"]:
        actor(state, aid).pop("moved_this_round", None)


def spend(state, aid, action, *, out_of_turn=False):
    if state["mode"] != "combat":
        return
    combat = state["combat"]
    if aid not in combat["actors"]:
        raise LoreKitError("Actor is not in this combat")
    a = actor(state, aid)
    if a["species"] == "human" and a["health"] == 0:
        raise LoreKitError("Broken human can only make one movement per round")
    if a.get("shutdown") or 5 in a.get("system_damage", []):
        raise LoreKitError("Synthetic is immobilized or shut down")
    if a["dead"] or a.get("stunned") or a.get("skip_turn") or 12 in a["panic_responses"]:
        raise LoreKitError("Actor cannot currently act")
    if out_of_turn and a.get("interrupt_blocked"):
        raise LoreKitError("Actor cannot interrupt")
    if not out_of_turn and aid not in combat["slots"][combat["index"]]["actors"]:
        raise LoreKitError("Not this actor's turn")
    if action not in ("full", "quick"):
        raise LoreKitError("Invalid action cost")
    if action == "quick" and 4 in a.get("system_damage", []):
        action = "full"
    b = combat["budgets"][aid]
    if action == "full":
        if b["full"] or b["quick"] >= 2:
            raise LoreKitError("Full action unavailable")
        b["full"] = True
    else:
        if b["quick"] >= (1 if b["full"] else 2):
            raise LoreKitError("Quick action unavailable")
        b["quick"] += 1


def hold_off(state, aid, card):
    combat = state["combat"]
    if not combat or aid not in combat["slots"][combat["index"]]["actors"]:
        raise LoreKitError("Only the current actor can hold off")
    current = combat["slots"][combat["index"]]
    later = next((s for s in combat["slots"][combat["index"] + 1 :] if s["card"] == card), None)
    if later is None:
        raise LoreKitError("Hold-off requires an uncompleted later initiative card")
    current["actors"], later["actors"] = later["actors"], current["actors"]
    return {"type": "held_off", "actor": aid, "card": card}


def move(state, aid, destination, bundle, *, forced=False):
    a = actor(state, aid)
    if not traversable(border(state, a["zone"], destination)):
        raise LoreKitError("No traversable border to destination")
    weight, limit = load_weight(a, bundle)
    if weight > limit * 2:
        raise LoreKitError("Cannot move above maximum encumbrance")
    if any(e["hook"] == "immobile" for i in a["injuries"] for e in i["effects"]):
        raise LoreKitError("Actor is immobilized")
    if not forced:
        if state["mode"] == "combat" and a["health"] == 0 and a["species"] == "human":
            combat = state["combat"]
            if aid not in combat["slots"][combat["index"]]["actors"] or a.get("broken_move_round") == combat["round"]:
                raise LoreKitError("Broken movement unavailable")
            a["broken_move_round"] = combat["round"]
        else:
            movement_full = 2 in a.get("system_damage", []) or any(
                e["hook"] == "movement_full" for i in a["injuries"] for e in i["effects"]
            )
            spend(state, aid, "full" if movement_full else "quick")
    for other in state["actors"].values():
        if aid in other.get("adjacent", []):
            other["adjacent"].remove(aid)
    a["adjacent"] = []
    a["hidden"] = False
    a["cover"] = None
    a["zone"] = destination
    key = "moved_this_round" if state["mode"] == "combat" else "moved_this_stretch"
    if weight > limit and not a.get(key):
        effects.stress(a, 1)
    a[key] = True
    if state["scene"] and aid not in state["scene"]["moved"]:
        state["scene"]["moved"].append(aid)
    return {"type": "moved", "actor": aid, "zone": destination}


def end_turn(state, aid, rng, bundle=None):
    combat = state["combat"]
    if not combat or aid not in combat["slots"][combat["index"]]["actors"]:
        raise LoreKitError("Only the current actor can end its turn")
    for current in combat["slots"][combat["index"]]["actors"]:
        a = actor(state, current)
        a.pop("skip_turn", None)
        a.pop("interrupt_blocked", None)
        a["panic_responses"] = [r for r in a["panic_responses"] if r not in (7, 8, 9)]
    combat["index"] += 1
    if combat["index"] == len(combat["slots"]):
        state["clocks"]["round"] += 1
        from lorekit.alien import acid, survival

        events = []
        for actor_id, current_actor in state["actors"].items():
            if not current_actor["dead"] and current_actor.get("acid"):
                events.extend(acid.tick(state, actor_id, bundle, rng))
        survival.due_events(state, "round")
        draw_round(state, rng)
    return {"type": "turn_ended", "combat": copy.deepcopy(combat), "events": locals().get("events", [])}


def approach(state, aid, target_id):
    a, target = actor(state, aid), actor(state, target_id)
    if aid == target_id or a["zone"] is None or a["zone"] != target["zone"]:
        raise LoreKitError("Approach requires another actor in the same zone")
    spend(state, aid, "quick")
    a.setdefault("adjacent", [])
    target.setdefault("adjacent", [])
    if target_id not in a["adjacent"]:
        a["adjacent"].append(target_id)
    if aid not in target["adjacent"]:
        target["adjacent"].append(aid)
    return {"type": "approached", "actor": aid, "target": target_id}
