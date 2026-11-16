"""Personal non-attack actions and their rule-check continuations."""

from __future__ import annotations

from lorekit.alien import checks, scenes, survival
from lorekit.alien.state import actor
from lorekit.db import LoreKitError


def act(state, intent, bundle, rng):
    aid = intent.get("actor")
    a = actor(state, aid)
    action = intent.get("action")
    if action in ("door_lock", "door_unlock", "door_peek", "door_hack", "door_break"):
        edge = scenes.border(state, a["zone"], intent.get("zone"))
        if not edge or not edge.get("door") or edge.get("sealed"):
            raise LoreKitError("Actor needs access to an unsealed door/hatch")
        if action == "door_hack" and not edge.get("electronic"):
            raise LoreKitError("Door does not have an electronic lock")
        scenes.spend(state, aid, "full" if action in ("door_hack", "door_break") else "quick")
        if action in ("door_hack", "door_break"):
            return checks.start(
                state,
                {
                    "actor": aid,
                    "skill": "comtech" if action == "door_hack" else "close_combat",
                    "modifier": 2 if action == "door_break" else 0,
                    "continuation": {"kind": "action", "action": action, "actor": aid, "zone": intent["zone"]},
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
            target["health"] == 0 or target.get("unconscious") or target.get("immobilized_by") or target.get("shutdown")
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
        entry, _ = survival.item(a, intent.get("item"), bundle)
        if not entry.get("dropped") or entry.get("ground_zone") != a["zone"]:
            raise LoreKitError("Dropped item is not in reach")
        if state["mode"] != "combat":
            if intent.get("stretch_completed") is not True:
                raise LoreKitError("Finding a lost item outside combat requires a completed stretch")
            return checks.start(
                state,
                {
                    "actor": aid,
                    "skill": "observation",
                    "continuation": {"kind": "action", "action": "find_item", "actor": aid, "item": entry["id"]},
                },
                bundle,
                rng,
            )
        scenes.spend(state, aid, "quick")
        entry["dropped"] = False
        entry["equipped"] = True
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
    if result["success"]:
        if action in ("door_hack", "door_break"):
            edge = scenes.border(state, a["zone"], continuation["zone"])
            if action == "door_hack":
                edge.update(locked=False)
            else:
                amount = max(0, result["successes"][0] - edge.get("armor", 2))
                edge["health"] = max(0, edge.get("health", 10) - amount)
                if edge["health"] == 0:
                    edge.update(locked=False, open=True, door=False)
        elif action == "drag":
            scenes.move(state, continuation["actor"], continuation["zone"], bundle, forced=True, rng=rng)
            scenes.move(state, continuation["target"], continuation["zone"], bundle, forced=True, rng=rng)
        elif action == "find_item":
            entry, _ = survival.item(a, continuation["item"], bundle)
            entry.update(dropped=False, equipped=True)
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
    return {"type": "action_result", "action": action, "success": result["success"], "checks": result["checks"]}
