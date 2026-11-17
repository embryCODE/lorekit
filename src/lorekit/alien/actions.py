"""Personal non-attack actions and their rule-check continuations."""

from __future__ import annotations

from lorekit.alien import checks, scenes, survival
from lorekit.alien.characters import incapacitated
from lorekit.alien.state import actor
from lorekit.db import LoreKitError


def act(state, intent, bundle, rng):
    aid = intent.get("actor")
    a = actor(state, aid)
    action = intent.get("action")
    if action == "take_object":
        obj = (state["scene"] or {}).get("objects", {}).get(intent.get("object"))
        definition = bundle["collections"].get("items", {}).get(obj.get("content_id")) if obj else None
        if not obj or not obj.get("found") or obj.get("taken_by") or obj["zone"] != a["zone"] or definition is None:
            raise LoreKitError("Choose a discovered supported item in this zone")
        instance_id = intent.get("item")
        if not isinstance(instance_id, str) or not instance_id or any(i["id"] == instance_id for i in a["inventory"]):
            raise LoreKitError("Choose a new inventory instance ID")
        scenes.spend(state, aid, "quick")
        a["inventory"].append(
            {
                "id": instance_id,
                "content_id": obj["content_id"],
                "quantity": 1,
                "supply": definition.get("capacity", 0),
                "reloads": 0,
                "equipped": "armor" not in definition and not definition.get("vacuum_proof"),
            }
        )
        obj["taken_by"] = aid
        return {"type": "object_taken", "actor": aid, "item": instance_id}
    if action == "drop":
        entry, _ = survival.item(a, intent.get("item"), bundle)
        if entry.get("dropped"):
            raise LoreKitError("Item is already on the ground")
        entry.update(dropped=True, equipped=False, ground_zone=a["zone"], lost=False)
        return {"type": "item_dropped", "actor": aid, "item": entry["id"]}
    if action == "consume":
        from lorekit.alien.consumables import consume

        return consume(state, intent, bundle)
    if action == "help":
        from lorekit.alien.characters import SKILLS

        target = actor(state, intent.get("target"))
        if not state["combat"] or intent.get("skill") not in SKILLS or target["zone"] != a["zone"]:
            raise LoreKitError("Declare a skill and another character in speaking/working range during combat")
        if target is a or 5 in a["panic_responses"] or 5 in target["panic_responses"]:
            raise LoreKitError("These characters cannot give/receive help")
        scenes.spend(state, aid, "full")
        a["help_offer"] = {"actor": intent["target"], "skill": intent["skill"], "round": state["combat"]["round"]}
    elif action in ("equip", "suit_enter"):
        entry, definition = survival.item(a, intent.get("item"), bundle)
        if entry.get("dropped"):
            raise LoreKitError("Item is not available")
        if action == "suit_enter":
            if not definition.get("vacuum_proof"):
                raise LoreKitError("Choose a vacuum-proof spacesuit")
            scenes.spend(state, aid, "full")
            return checks.start(
                state,
                {
                    "actor": aid,
                    "skill": "mobility",
                    "continuation": {"kind": "action", "action": action, "actor": aid, "item": entry["id"]},
                },
                bundle,
                rng,
            )
        if type(intent.get("equipped")) is not bool:
            raise LoreKitError("Choose equipped:true or equipped:false")
        vent = (state["scene"] or {}).get("vents", {}).get(a["zone"], {})
        if (
            intent["equipped"]
            and definition.get("vacuum_proof")
            and (vent.get("vacuum") or any(h["id"] == "vacuum" for h in a.get("hazards", [])))
        ):
            raise LoreKitError("Entering a spacesuit in vacuum requires suit_enter and its Mobility check")
        scenes.spend(state, aid, "quick")
        entry["equipped"] = intent["equipped"]
    elif action == "seal_access":
        edge = scenes.border(state, a["zone"], intent.get("zone"))
        entry, definition = survival.item(a, intent.get("item"), bundle)
        if (
            not edge
            or edge.get("sealed")
            or not definition.get("cutting_torch")
            or not entry.get("equipped")
            or entry.get("dropped")
            or not entry["supply"]
        ):
            raise LoreKitError("Sealing requires an accessible point and an equipped powered cutting torch")
        if a.get("seal_attempt_stretch") == state["clocks"]["stretch"]:
            raise LoreKitError("Only one access-sealing attempt is allowed per stretch")
        a["seal_attempt_stretch"] = state["clocks"]["stretch"]
        scenes.spend(state, aid, "full")
        return checks.start(
            state,
            {
                "actor": aid,
                "skill": "heavy_machinery",
                "context": "seal_access",
                "continuation": {
                    "kind": "action",
                    "action": action,
                    "actor": aid,
                    "zone": intent["zone"],
                    "item": entry["id"],
                },
            },
            bundle,
            rng,
        )
    elif action in ("block", "unblock", "allow_pass"):
        edge = scenes.border(state, a["zone"], intent.get("zone"))
        if not edge or incapacitated(a):
            raise LoreKitError("Only an active character at a mapped passage can block it")
        blockers = edge.setdefault("blockers", [])
        if action == "block":
            scenes.spend(state, aid, "quick")
            if aid not in blockers:
                blockers.append(aid)
        elif action == "unblock":
            if aid in blockers:
                blockers.remove(aid)
        else:
            if aid not in blockers:
                raise LoreKitError("Actor is not blocking this passage")
            actor(state, intent.get("target")).setdefault("passed", []).append(aid)
    elif action in ("door_lock", "door_unlock", "door_peek", "door_hack", "door_break"):
        edge = scenes.border(state, a["zone"], intent.get("zone"))
        if not edge or not edge.get("door") or edge.get("sealed"):
            raise LoreKitError("Actor needs access to an unsealed door/hatch")
        if action == "door_hack" and not edge.get("electronic"):
            raise LoreKitError("Door does not have an electronic lock")
        scenes.spend(state, aid, "full" if action in ("door_hack", "door_break") else "quick")
        if action in ("door_hack", "door_break"):
            weapon = None
            definition = {"damage": 1, "modifier": 0}
            if action == "door_break" and intent.get("weapon") is not None:
                weapon, definition = survival.item(a, intent["weapon"], bundle)
                if (
                    not weapon.get("equipped")
                    or weapon.get("dropped")
                    or "damage" not in definition
                    or weapon.get("jammed")
                    or (definition.get("supply_type") and not weapon["supply"])
                ):
                    raise LoreKitError("Choose an available weapon for breaking the door")
            return checks.start(
                state,
                {
                    "actor": aid,
                    "skill": "comtech" if action == "door_hack" else "close_combat",
                    "modifier": 2 + definition.get("modifier", 0) - 2 * definition.get("minimum", 0)
                    if action == "door_break"
                    else 0,
                    "continuation": {
                        "kind": "action",
                        "action": action,
                        "actor": aid,
                        "zone": intent["zone"],
                        "damage": definition["damage"],
                        "piercing": definition.get("piercing", False),
                        "weapon": weapon["id"] if weapon else None,
                    },
                },
                bundle,
                rng,
            )
        if action == "door_lock":
            edge.update(locked=True, open=False)
        elif action == "door_unlock":
            if edge.get("locked") and intent.get("key") is not True:
                raise LoreKitError("Unlock requires the key or a successful hack/break action")
            edge["locked"] = False
        else:
            if edge.get("locked"):
                raise LoreKitError("Cannot peek through a locked door")
            edge["peek"] = True
    elif action == "drag":
        target_id = intent.get("target")
        target = actor(state, target_id)
        if scenes.range_band(state, aid, target_id) != 0 or not (
            incapacitated(target)
            or target.get("immobilized_by")
            or any(e["hook"] == "immobile" for injury in target["injuries"] for e in injury["effects"])
        ):
            raise LoreKitError("Drag a broken or immobilized person at Adjacent range")
        if not scenes.traversable(scenes.border(state, a["zone"], intent.get("zone"))):
            raise LoreKitError("No traversable dragging route")
        scenes.spend(state, aid, "quick")
        return checks.start(
            state,
            {
                "actor": aid,
                "skill": "stamina",
                "continuation": {
                    "kind": "action",
                    "action": action,
                    "actor": aid,
                    "target": target_id,
                    "zone": intent["zone"],
                },
            },
            bundle,
            rng,
        )
    elif action == "air_active":
        entry, definition = survival.item(a, intent.get("item"), bundle)
        if definition.get("supply_type") != "air" or type(intent.get("active")) is not bool:
            raise LoreKitError("air_active needs an air supply and an explicit boolean")
        entry["active_air"] = intent["active"]
        survival.sync_air(state, aid)
    elif action == "reload":
        entry, definition = survival.item(a, intent.get("item"), bundle)
        if definition.get("supply_type") not in ("ammo", "power") or not definition.get("capacity"):
            raise LoreKitError("Item cannot be reloaded")
        if entry.get("dropped") or entry["reloads"] < 1:
            raise LoreKitError("No available reload")
        scenes.spend(state, aid, "quick")
        entry["reloads"] -= 1
        entry["supply"] = definition["capacity"]
    elif action == "clear_jam":
        entry, _ = survival.item(a, intent.get("item"), bundle)
        if not entry.get("jammed"):
            raise LoreKitError("Item is not jammed")
        scenes.spend(state, aid, "full")
        return checks.start(
            state,
            {
                "actor": aid,
                "skill": "heavy_machinery",
                "continuation": {"kind": "action", "action": action, "actor": aid, "item": entry["id"]},
            },
            bundle,
            rng,
        )
    elif action == "cover":
        from lorekit.alien.effects import refresh_overkill

        refresh_overkill(state, aid)
        if a.get("overkill"):
            raise LoreKitError("Overkill prevents taking cover")
        kind = intent.get("cover")
        if kind not in ("partial", "full") or not state["scene"]["zones"][a["zone"]].get("cluttered"):
            raise LoreKitError("Cover requires a cluttered zone and partial/full choice")
        scenes.spend(state, aid, "quick" if kind == "partial" else "full")
        a["cover"] = kind
        if kind == "full":
            a["barrier_armor"] = intent.get("barrier_armor", 0)
            from lorekit.alien.state import integer

            integer(a["barrier_armor"], "cover barrier armor")
    elif action == "pickup":
        owner_id = intent.get("owner", aid)
        owner = actor(state, owner_id)
        entry, definition = survival.item(owner, intent.get("item"), bundle)
        if not entry.get("dropped") or entry.get("ground_zone") != a["zone"]:
            raise LoreKitError("Dropped item is not in reach")
        new_id = intent.get("new_item_id", entry["id"])
        if owner_id != aid and (
            not isinstance(new_id, str) or not new_id or any(i["id"] == new_id for i in a["inventory"])
        ):
            raise LoreKitError("Choose an unused new_item_id for the transferred item")
        if state["mode"] != "combat" and entry.get("lost"):
            if intent.get("stretch_completed") is not True:
                raise LoreKitError("Finding a lost item outside combat requires a completed stretch")
            return checks.start(
                state,
                {
                    "actor": aid,
                    "skill": "observation",
                    "continuation": {
                        "kind": "action",
                        "action": "find_item",
                        "actor": aid,
                        "owner": owner_id,
                        "item": entry["id"],
                        "new_item_id": new_id,
                    },
                },
                bundle,
                rng,
            )
        scenes.spend(state, aid, "quick")
        take_inventory(state, aid, owner_id, entry, definition, new_id)
    elif action == "release":
        target_id = a.get("grappling")
        if not target_id:
            raise LoreKitError("Actor is not grappling")
        actor(state, target_id).pop("grappled_by", None)
        a.pop("grappling", None)
    elif action == "escape":
        grappler_id = a.get("grappled_by")
        if not grappler_id:
            raise LoreKitError("Actor is not grappled")
        scenes.spend(state, aid, "full", escape=True)
        return checks.start(
            state,
            {
                "actor": aid,
                "skill": "mobility" if a["species"] == "xenomorph" else "close_combat",
                "opponent": {
                    "actor": grappler_id,
                    "skill": "mobility" if actor(state, grappler_id)["species"] == "xenomorph" else "close_combat",
                },
                "continuation": {"kind": "action", "action": action, "actor": aid, "grappler": grappler_id},
            },
            bundle,
            rng,
        )
    elif action == "orders":
        target_id = intent.get("target")
        target = actor(state, target_id)
        if a["zone"] != target["zone"] and intent.get("comms") is not True:
            raise LoreKitError("Orders require speaking range or comms")
        if not isinstance(intent.get("order"), str) or not intent["order"].strip():
            raise LoreKitError("An explicit order is required")
        from lorekit.alien.characters import talents

        scenes.spend(state, aid, "quick" if any(e["hook"] == "field_commander" for e in talents(a, bundle)) else "full")
        return checks.start(
            state,
            {
                "actor": aid,
                "skill": "command",
                "continuation": {
                    "kind": "action",
                    "action": action,
                    "actor": aid,
                    "target": target_id,
                    "order": intent["order"],
                },
            },
            bundle,
            rng,
        )
    else:
        raise LoreKitError("Unsupported personal action")
    return {"type": "action", "actor": aid, "action": action}


def finish(state, result, bundle, rng=None):
    continuation = result["continuation"]
    a = actor(state, continuation["actor"])
    action = continuation["action"]
    events = []
    if action == "door_break" and continuation.get("weapon"):
        entry, definition = survival.item(a, continuation["weapon"], bundle)
        if definition.get("single_shot"):
            entry["supply"] = 0
        elif definition.get("supply_type"):
            events.append(survival.supply(a, entry["id"], bundle, rng))
    if action == "seal_access":
        events.append(survival.supply(a, continuation["item"], bundle, rng))
    if result["success"]:
        if action == "suit_enter":
            entry, _ = survival.item(a, continuation["item"], bundle)
            entry.update(equipped=True, active_air=True)
            if any(h["id"] == "vacuum" for h in a.get("hazards", [])):
                survival.remedy(
                    state, {"actor": continuation["actor"], "hazard": "vacuum", "reason": "Sealed spacesuit"}
                )
            survival.sync_air(state, continuation["actor"])
        elif action == "seal_access":
            scenes.border(state, a["zone"], continuation["zone"]).update(sealed=True, open=False)
        elif action in ("door_hack", "door_break"):
            edge = scenes.border(state, a["zone"], continuation["zone"])
            if action == "door_hack":
                edge.update(locked=False)
            else:
                armor = max(0, edge.get("armor", 2) - int(continuation.get("piercing", False)))
                base = continuation.get("damage", 1)
                amount = max(0, base + result["successes"][0] - 1 - armor) if base >= armor else 0
                edge["health"] = max(0, edge.get("health", 10) - amount)
                if edge["health"] == 0:
                    edge.update(locked=False, open=True, door=False)
        elif action == "drag":
            events.append(scenes.move(state, continuation["actor"], continuation["zone"], bundle, forced=True, rng=rng))
            events.append(
                scenes.move(state, continuation["target"], continuation["zone"], bundle, forced=True, rng=rng)
            )
        elif action == "find_item":
            owner_id = continuation.get("owner", continuation["actor"])
            entry, definition = survival.item(actor(state, owner_id), continuation["item"], bundle)
            take_inventory(
                state, continuation["actor"], owner_id, entry, definition, continuation.get("new_item_id", entry["id"])
            )
        elif action == "clear_jam":
            entry, _ = survival.item(a, continuation["item"], bundle)
            entry["jammed"] = False
        elif action == "escape":
            a.pop("grappled_by", None)
            actor(state, continuation["grappler"]).pop("grappling", None)
        elif action == "orders":
            target = actor(state, continuation["target"])
            target.setdefault("orders", []).append(
                {
                    "order": continuation["order"],
                    "bonus": result["successes"][0],
                    "round": state["combat"]["round"] if state["combat"] else None,
                }
            )
    return {
        "type": "action_result",
        "action": action,
        "success": result["success"],
        "checks": result["checks"],
        "events": events,
    }


def take_inventory(state, recipient, owner, entry, definition, new_id):
    if recipient != owner:
        actor(state, owner)["inventory"].remove(entry)
        actor(state, recipient)["inventory"].append(entry)
        entry["id"] = new_id
    entry.update(dropped=False, lost=False, equipped="armor" not in definition and not definition.get("vacuum_proof"))
