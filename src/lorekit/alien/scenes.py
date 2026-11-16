"""Zone topology, initiative slots and action accounting for Alien Evolved."""

from __future__ import annotations

import copy
from collections import deque

from lorekit.alien import effects
from lorekit.alien.characters import load_weight, talents
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def setup(state, spec):
    zones = spec.get("zones")
    borders = spec.get("borders", [])
    if not isinstance(zones, dict) or not zones or any(not isinstance(k, str) or not k for k in zones):
        raise LoreKitError("Scene requires named zone objects")
    for zone in zones.values():
        if not isinstance(zone, dict) or set(zone) - {
            "cluttered",
            "dark",
            "crawlspace",
            "zero_g",
            "flammable",
            "floor_armor",
            "outer_armor",
            "auto_seal",
            "body_temperature",
            "capacity",
        }:
            raise LoreKitError("Unsupported zone property")
        for surface in ("floor_armor", "outer_armor", "capacity"):
            if surface in zone:
                integer(zone[surface], surface)
        if any(type(v) is not bool for k, v in zone.items() if k not in ("floor_armor", "outer_armor", "capacity")):
            raise LoreKitError("Zone flags must be boolean")
    if not isinstance(borders, list):
        raise LoreKitError("borders must be a list")
    seen = set()
    for b in borders:
        if not isinstance(b, dict) or set(b) - {
            "a",
            "b",
            "open",
            "door",
            "locked",
            "sealed",
            "peek",
            "armor",
            "health",
            "electronic",
            "ends",
            "blockers",
        }:
            raise LoreKitError("Invalid scene border")
        if b.get("a") not in zones or b.get("b") not in zones or b["a"] == b["b"]:
            raise LoreKitError("Invalid border endpoints")
        pair = tuple(sorted((b["a"], b["b"])))
        if pair in seen:
            raise LoreKitError("Duplicate border")
        seen.add(pair)
        if "ends" in b and (
            not isinstance(b["ends"], dict)
            or any(z not in (b["a"], b["b"]) or end not in ("head", "tail") for z, end in b["ends"].items())
        ):
            raise LoreKitError("Invalid crawlspace border ends")
        if "blockers" in b:
            if not isinstance(b["blockers"], list) or any(bid not in state["actors"] for bid in b["blockers"]):
                raise LoreKitError("Invalid passage blockers")
        for number in ("armor", "health"):
            if number in b:
                integer(b[number], "door " + number)
        if any(type(b[k]) is not bool for k in set(b) - {"a", "b", "armor", "health", "ends", "blockers"}):
            raise LoreKitError("Border flags must be boolean")
    sight = spec.get("sight", [])
    if not isinstance(sight, list) or any(
        not isinstance(pair, list) or len(pair) != 2 or pair[0] not in zones or pair[1] not in zones for pair in sight
    ):
        raise LoreKitError("sight must contain zone pairs with a GM-confirmed clear line")
    for current in state["actors"].values():
        current["adjacent"] = []
    for aid, zid in spec.get("placements", {}).items():
        a = actor(state, aid)
        if zid not in zones:
            raise LoreKitError("Unknown placement zone")
        a["zone"] = zid
        a["adjacent"] = []
    if any(a["zone"] is not None and a["zone"] not in zones for a in state["actors"].values()):
        raise LoreKitError("Reposition actors whose old zone is absent from the replacement scene")
    crawl_order = copy.deepcopy(spec.get("crawl_order", {}))
    if not isinstance(crawl_order, dict) or set(crawl_order) - set(zones):
        raise LoreKitError("Invalid crawlspace order map")
    for zone, definition in zones.items():
        if definition.get("crawlspace"):
            occupants = [aid for aid, a in state["actors"].items() if a["zone"] == zone]
            order = crawl_order.setdefault(zone, occupants if len(occupants) <= 1 else [])
            if not isinstance(order, list) or len(set(order)) != len(order) or set(order) != set(occupants):
                raise LoreKitError("Declare every crawlspace occupant in head-to-tail order")
    objects = copy.deepcopy(spec.get("objects", {}))
    if not isinstance(objects, dict):
        raise LoreKitError("Scene objects must be keyed by stable ID")
    for key, obj in objects.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(obj, dict)
            or set(obj) - {"zone", "description", "content_id", "found"}
            or obj.get("zone") not in zones
            or not isinstance(obj.get("description"), str)
            or type(obj.get("found", False)) is not bool
        ):
            raise LoreKitError("Invalid scene object")
        obj.setdefault("found", False)
    state["scene"] = {
        "zones": copy.deepcopy(zones),
        "crawl_order": crawl_order,
        "objects": objects,
        "borders": copy.deepcopy(borders),
        "phase": "pc",
        "sight": copy.deepcopy(sight),
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
    if a.get("unconscious") or a["zone"] is None or b["zone"] is None or b["hidden"] or b.get("cover") == "full":
        return False
    if a["zone"] == b["zone"]:
        return True
    if any(set(pair) == {a["zone"], b["zone"]} for pair in state["scene"].get("sight", [])):
        return True
    edge = border(state, a["zone"], b["zone"])
    return bool(edge and (edge.get("open") or edge.get("peek")) and not edge.get("sealed"))


def range_band(state, aid, bid):
    a, b = actor(state, aid), actor(state, bid)
    d = distance(state, a["zone"], b["zone"])
    if d == 0:
        return 0 if bid in a.get("adjacent", []) else 1
    return 2 if d == 1 else 3 if d <= 4 else 4


def start_combat(state, intent, rng, bundle=None):
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
    if any(len({actor(state, aid).get("speed", 1) for aid in group}) != 1 for group in groups):
        raise LoreKitError("NPC initiative groups require matching Speed ratings")
    units = [list(g) for g in groups] + [[aid] for aid in participants if aid not in flattened]
    state["combat"] = {"actors": participants[:], "units": units, "round": 0, "slots": [], "index": 0, "budgets": {}}
    state["mode"] = "combat"
    for aid in participants:
        a = actor(state, aid)
        for injury in a["injuries"]:
            if injury["interval"] == "round" and not injury["stabilized"]:
                injury["due_unit"] = "turn"
                injury["due"] = a.get("turns_completed", 0) + 1
    ambush = intent.get("ambush", [])
    surprise = list(dict.fromkeys(intent.get("surprise", []) + ambush))
    for aid in ambush:
        actor(state, aid)["ambush_targets"] = [tid for tid in participants if tid not in ambush]
    draw_round(state, rng, surprise, bundle)
    return {"type": "combat_started", "combat": copy.deepcopy(state["combat"])}


def draw_round(state, rng, surprise=None, bundle=None):
    combat = state["combat"]
    expanded = []
    for original in combat["units"]:
        unit = [aid for aid in original if not actor(state, aid)["dead"] and not actor(state, aid).get("escaped")]
        if not unit:
            continue
        speed = actor(state, unit[0]).get("speed", 1)
        for _ in range(speed):
            expanded.append(unit)
    if not expanded:
        if combat["round"] == 0:
            raise LoreKitError("Combat needs an actor with an initiative turn")
        finish_combat(state)
        return
    if len(expanded) > 10:
        raise LoreKitError("More than ten initiative draws: group NPCs or record a ruling")
    if combat["round"] > 0 and state["options"]["fixed_initiative"]:
        remaining = copy.deepcopy(expanded)
        kept = []
        for slot in combat["slots"]:
            if slot["actors"] in remaining:
                kept.append(slot)
                remaining.remove(slot["actors"])
        deck = [card for card in range(1, 11) if card not in {slot["card"] for slot in kept}]
        rng.shuffle(deck)
        for unit in remaining:
            kept.append({"card": deck.pop(), "actors": unit})
        combat["slots"] = sorted(kept, key=lambda slot: slot["card"])
        combat["index"] = 0
        combat["round"] += 1
        combat["budgets"] = {aid: {"full": False, "quick": 0} for aid in combat["actors"]}
        for aid in combat["actors"]:
            actor(state, aid).pop("moved_this_round", None)
            actor(state, aid)["orders"] = []
        return
    if bundle:
        from lorekit.alien import initiative

        if any(
            initiative.has(state, unit, bundle, hook)
            for unit in expanded
            for hook in ("fast_reflexes", "combat_veteran")
        ):
            initiative.start(state, expanded, bundle, rng, surprise)
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
        actor(state, aid)["orders"] = []


def spend(state, aid, action, *, out_of_turn=False, escape=False, grapple_attack=False):
    if state["mode"] != "combat" or actor(state, aid).get("escaped"):
        return
    combat = state["combat"]
    if aid not in combat["actors"]:
        raise LoreKitError("Actor is not in this combat")
    a = actor(state, aid)
    if a.get("grappled_by") and not escape:
        raise LoreKitError("Grappled actor may only attempt escape")
    if a.get("grappling") and not grapple_attack:
        raise LoreKitError("Grappler may only attack its held target or release")
    if a["species"] == "xenomorph":
        if a.get("frozen") or a.get("attached_to") or a.get("speed", 1) == 0:
            raise LoreKitError("Creature cannot act while attached, frozen or immobile")
        if a.get("last_breath"):
            raise LoreKitError("Last Breath permits only its final signature attack")
        if out_of_turn or aid not in combat["slots"][combat["index"]]["actors"]:
            raise LoreKitError("Creature cannot interrupt or act outside its slot")
        token = f"{combat['round']}:{combat['index']}"
        if a.get("acted_slot") == token or a["dead"]:
            raise LoreKitError("Creature slot action unavailable")
        a["acted_slot"] = token
        for victim in state["actors"].values():
            if victim.get("immobilized_by") == aid:
                victim.pop("immobilized_by")
        return
    if a["species"] == "human" and a["health"] == 0:
        raise LoreKitError("Broken human can only make one movement per round")
    if a.get("shutdown") or 5 in a.get("system_damage", []):
        raise LoreKitError("Synthetic is immobilized or shut down")
    if a["dead"] or a.get("unconscious") or a.get("stunned") or a.get("skip_turn") or 12 in a["panic_responses"]:
        raise LoreKitError("Actor cannot currently act")
    if not out_of_turn and any(r in a["panic_responses"] for r in (10, 11)):
        raise LoreKitError("Flee/Frenzy permits only its forced action until panic stops")
    if out_of_turn and (a.get("interrupt_blocked") or any(r >= 7 for r in a["panic_responses"])):
        raise LoreKitError("Actor cannot interrupt")
    if not out_of_turn and aid not in combat["slots"][combat["index"]]["actors"]:
        raise LoreKitError("Not this actor's turn")
    if action not in ("full", "quick"):
        raise LoreKitError("Invalid action cost")
    if action == "quick" and 4 in a.get("system_damage", []):
        action = "full"
    if out_of_turn and action == "full" and aid not in combat["slots"][combat["index"]]["actors"]:
        raise LoreKitError("A full action cannot interrupt another actor's turn")
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
    if actor(state, aid).get("entered_turn") == f"{combat['round']}:{combat['index']}":
        raise LoreKitError("Hold off before beginning turn effects or actions")
    current = combat["slots"][combat["index"]]
    later = next((s for s in combat["slots"][combat["index"] + 1 :] if s["card"] == card), None)
    if later is None:
        raise LoreKitError("Hold-off requires an uncompleted later initiative card")
    current["actors"], later["actors"] = later["actors"], current["actors"]
    return {"type": "held_off", "actor": aid, "card": card}


def move(state, aid, destination, bundle, *, forced=False, rng=None, zero_g_checked=False):
    a = actor(state, aid)
    if a.get("immobilized_by") and not actor(state, a["immobilized_by"])["dead"]:
        raise LoreKitError("A creature tail hold prevents movement")
    if not forced and (
        a["dead"]
        or a.get("stunned")
        or a.get("unconscious")
        or a.get("shutdown")
        or a.get("frozen")
        or a.get("attached_to")
        or a["species"] == "xenomorph"
        and a.get("speed", 1) == 0
    ):
        raise LoreKitError("Actor cannot move voluntarily")
    edge = route(state, aid, destination)
    if destination in state["scene"].get("fires", {}) and rng is None:
        raise LoreKitError("Moving into fire requires the environmental resolver")
    zero_g = state["scene"]["zones"][a["zone"]].get("zero_g")
    if (
        zero_g
        and not forced
        and not zero_g_checked
        and not any(e["hook"] == "zero_g_immunity" for e in talents(a, bundle))
    ):
        raise LoreKitError("Zero-G movement requires a movement check operation")
    weight, limit = load_weight(a, bundle)
    if weight > limit * 2:
        raise LoreKitError("Cannot move above maximum encumbrance")
    if any(e["hook"] == "immobile" for i in a["injuries"] for e in i["effects"]):
        raise LoreKitError("Actor is immobilized")
    if not forced:
        if any(r in a["panic_responses"] for r in (10, 11)):
            raise LoreKitError("Resolve forced panic movement instead")
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
    order = state["scene"].setdefault("crawl_order", {})
    if a["zone"] in order and aid in order[a["zone"]]:
        order[a["zone"]].remove(aid)
    if state["scene"]["zones"][destination].get("crawlspace"):
        target_order = order.setdefault(destination, [])
        target_order.insert(0 if edge["ends"][destination] == "head" else len(target_order), aid)
    for border_entry in state["scene"]["borders"]:
        if aid in border_entry.get("blockers", []):
            border_entry["blockers"].remove(aid)
    a.pop("passed", None)
    a["zone"] = destination
    for parasite_id, parasite in state["actors"].items():
        if parasite.get("attached_to") == aid:
            for other in state["actors"].values():
                if parasite_id in other.get("adjacent", []):
                    other["adjacent"].remove(parasite_id)
            parasite["zone"] = destination
            parasite["adjacent"] = [aid]
            a.setdefault("adjacent", []).append(parasite_id)
    key = "moved_this_round" if state["mode"] == "combat" and not a.get("escaped") else "moved_this_stretch"
    if weight > limit:
        effects.stress(a, 1)
    a[key] = True
    if state["scene"] and aid not in state["scene"]["moved"]:
        state["scene"]["moved"].append(aid)
    result = {"type": "moved", "actor": aid, "zone": destination}
    if destination in state["scene"].get("fires", {}):
        from lorekit.alien.environment import fire_attack

        result["fire"] = fire_attack(state, aid, state["scene"]["fires"][destination], bundle, rng)
    return result


def end_turn(state, aid, rng, bundle=None):
    combat = state["combat"]
    if not combat or aid not in combat["slots"][combat["index"]]["actors"]:
        raise LoreKitError("Only the current actor can end its turn")
    for current in combat["slots"][combat["index"]]["actors"]:
        a = actor(state, current)
        if a.get("last_breath") and not a["dead"]:
            raise LoreKitError("Resolve the final Last Breath signature before ending the turn")
        a["turns_completed"] = a.get("turns_completed", 0) + 1
        for injury in a["injuries"]:
            if (
                injury["lethal"]
                and not injury["stabilized"]
                and injury.get("due_unit") == "turn"
                and injury["due"] <= a["turns_completed"]
            ):
                state["queue"].append({"type": "due", "kind": "death", "actor": current, "injury": injury["id"]})
                injury["due"] = a["turns_completed"] + 1
        effects.refresh_overkill(state, current)
        if a.get("overkill"):
            visible = [
                tid
                for tid in a["overkill"]
                if not actor(state, tid)["dead"]
                and actor(state, tid)["health"] > 0
                and line_of_sight(state, current, tid)
            ]
            if visible and not a.get("overkill_attacked"):
                raise LoreKitError("Overkill requires attacking a visible enemy before ending the turn")
            if not visible or a["health"] == 0:
                a.pop("overkill", None)
            a.pop("overkill_attacked", None)
        a.pop("escape_required", None)
        a.pop("skip_turn", None)
        a.pop("interrupt_blocked", None)
        a["panic_responses"] = [r for r in a["panic_responses"] if r not in (7, 8, 9)]
    combat["index"] += 1
    if combat["index"] == len(combat["slots"]):
        state["clocks"]["round"] += 1
        from lorekit.alien import acid, creatures, environment, survival

        events = []
        for actor_id, current_actor in state["actors"].items():
            if not current_actor["dead"] and current_actor.get("acid"):
                events.extend(acid.tick(state, actor_id, bundle, rng))
        events.extend(acid.tick_floors(state, rng))
        events.extend(environment.round_start(state, bundle, rng))
        events.extend(creatures.egg_round(state, bundle, rng))
        survival.due_events(state, "round")
        draw_round(state, rng, bundle=bundle)
    events = locals().get("events", [])
    events.extend(effects.drain(state, rng, bundle=bundle))
    return {"type": "turn_ended", "combat": copy.deepcopy(state["combat"]), "events": events}


def approach(state, aid, target_id, bundle=None, *, paid=False):
    a, target = actor(state, aid), actor(state, target_id)
    if aid == target_id or a["zone"] is None or a["zone"] != target["zone"]:
        raise LoreKitError("Approach requires another actor in the same zone")
    if bundle:
        weight, limit = load_weight(a, bundle)
        if weight > 2 * limit or any(e["hook"] == "immobile" for i in a["injuries"] for e in i["effects"]):
            raise LoreKitError("Actor cannot approach while immobilized or above maximum encumbrance")
        if weight > limit:
            effects.stress(a, 1)
    movement_full = 2 in a.get("system_damage", []) or any(
        e["hook"] == "movement_full" for i in a["injuries"] for e in i["effects"]
    )
    if not paid:
        spend(state, aid, "full" if movement_full else "quick")
    a.setdefault("adjacent", [])
    target.setdefault("adjacent", [])
    if target_id not in a["adjacent"]:
        a["adjacent"].append(target_id)
    if aid not in target["adjacent"]:
        target["adjacent"].append(aid)
    return {"type": "approached", "actor": aid, "target": target_id}


def prepare_turn(state, only=None):
    if state["pending"] is not None or state["combat"] is None:
        return
    combat = state["combat"]
    for aid in combat["slots"][combat["index"]]["actors"]:
        if only is not None and aid != only:
            continue
        a = actor(state, aid)
        from lorekit.alien.creatures import egg_adjacent
        from lorekit.alien.effects import refresh_overkill

        refresh_overkill(state, aid)
        if a["species"] == "xenomorph" and not a["dead"]:
            if a.get("rise_next_turn"):
                a["health"] = 1
                a.pop("rise_next_turn")
            if a.get("escape_check"):
                state["queue"].append({"type": "wounded_escape", "actor": aid})
        egg_adjacent(state, aid)
        if not a["dead"] and any(r in a["panic_responses"] for r in (10, 11)):
            state["queue"].append(
                {"type": "panic_turn", "actor": aid, "result": max(r for r in a["panic_responses"] if r in (10, 11))}
            )
        if (
            a.get("stunned")
            and not a["dead"]
            and not a.get("shutdown")
            and (a["health"] > 0 or a["species"] == "synthetic")
        ):
            state["queue"].append({"type": "due", "kind": "stun_recovery", "actor": aid})


def route(state, aid, destination):
    """Validate GM-declared passage geometry, including ordered crawlspaces."""
    a = actor(state, aid)
    edge = border(state, a["zone"], destination)
    if not traversable(edge):
        raise LoreKitError("No traversable border to destination")
    for bid in edge.get("blockers", []):
        blocker = actor(state, bid)
        if bid != aid and not blocker["dead"] and blocker["health"] > 0 and bid not in a.get("passed", []):
            raise LoreKitError("A character blocks this passage; pass them with a special attack or permission")
    source = a["zone"]
    if state["scene"]["zones"][source].get("crawlspace"):
        order = state["scene"].get("crawl_order", {}).get(source, [])
        end = edge.get("ends", {}).get(source)
        if end not in ("head", "tail") or aid not in order:
            raise LoreKitError("Declare crawlspace ordering and the exit end")
        if aid != (order[0] if end == "head" else order[-1]):
            raise LoreKitError("Cannot move past another character in a crawlspace")
    if state["scene"]["zones"][destination].get("crawlspace"):
        if edge.get("ends", {}).get(destination) not in ("head", "tail"):
            raise LoreKitError("Declare the crawlspace entry end")
        capacity = state["scene"]["zones"][destination].get("capacity")
        if capacity is not None and len(state["scene"].get("crawl_order", {}).get(destination, [])) >= capacity:
            raise LoreKitError("No room to enter this crawlspace")
    return edge


def enter_combat(state, intent, bundle, rng):
    if state["combat"] is None:
        return start_combat(state, intent, rng, bundle)
    # Beta 2 does not specify reinforcements' initiative. B18: join next round,
    # preserving all cards/action budgets already used in the current round.
    joined = []
    for aid in intent["actors"]:
        a = actor(state, aid)
        a.pop("escaped", None)
        if aid not in state["combat"]["actors"]:
            state["combat"]["actors"].append(aid)
            state["combat"]["units"].append([aid])
            state["combat"]["budgets"][aid] = {"full": False, "quick": 0}
            joined.append(aid)
    for aid in intent.get("ambush", []):
        actor(state, aid)["ambush_targets"] = [tid for tid in intent["actors"] if tid not in intent["ambush"]]
    return {"type": "combat_joined", "actors": joined, "initiative": "next_round", "ruling": "B18"}


def finish_combat(state):
    for a in state["actors"].values():
        a.pop("escaped", None)
        for injury in a["injuries"]:
            if injury.get("due_unit") == "turn":
                injury["due_unit"] = "round"
                injury["due"] = state["clocks"]["round"] + 1
    state["combat"] = None
    state["mode"] = "stealth"


def movement_cost(a):
    return (
        "full"
        if 2 in a.get("system_damage", [])
        or any(e["hook"] == "movement_full" for i in a["injuries"] for e in i["effects"])
        else "quick"
    )
