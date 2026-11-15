"""Alien command router. Unsupported operations fail before canonical state writes."""

from __future__ import annotations

import re

from lorekit.alien import characters, checks, combat, content, creatures, decisions, effects, scenes, stealth, survival
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def transition(state: dict, intent: dict, rng) -> dict:
    op = intent.get("op")
    if state["pending"] is not None and op not in (
        "check_decide",
        "panic_decide",
        "defense_decide",
        "burst_decide",
        "detection_decide",
        "signature_decide",
        "override",
    ):
        raise LoreKitError("Resolve the pending decision before another operation")
    if state["queue"] and op not in (
        "queue_continue",
        "check_decide",
        "panic_decide",
        "defense_decide",
        "burst_decide",
        "detection_decide",
        "signature_decide",
        "override",
    ):
        raise LoreKitError("Resolve the response queue before another operation")
    bundle = content.read(state["content_hash"])
    if op == "override":
        reason = intent.get("reason")
        changes = intent.get("changes", {})
        if not isinstance(reason, str) or not reason.strip() or not isinstance(changes, dict):
            raise LoreKitError("A GM override requires a reason and an object of changes")
        if set(changes) - {"health", "stress", "armor", "dead", "zone", "hidden"}:
            raise LoreKitError("Unsupported override field; profile/content cannot be patched")
        if changes:
            target = actor(state, intent.get("actor"))
            if "armor" in changes:
                integer(changes["armor"], "armor")
            if "zone" in changes and (not state["scene"] or changes["zone"] not in state["scene"]["zones"]):
                raise LoreKitError("Override zone does not exist")
            target.update(changes)
        if intent.get("clear_pending"):
            state["pending"] = None
            state["queue"] = []
        return {
            "type": "gm_override",
            "reason": reason,
            "actor": intent.get("actor"),
            "changes": changes,
            "cleared_pending": bool(intent.get("clear_pending")),
        }
    if op == "content_import":
        replacement = content.read(intent.get("hash"))
        if intent.get("hash") is None:
            raise LoreKitError("A content hash is required")
        if state["actors"]:
            raise LoreKitError("Content changes with existing actors require an explicit migration")
        state["content_hash"] = intent["hash"]
        return {"type": "content_imported", "hash": intent["hash"]}
    if op == "configure":
        options = intent.get("options")
        if not isinstance(options, dict) or set(options) - set(state["options"]):
            raise LoreKitError("Unknown Alien session option")
        if any(type(v) is not bool for v in options.values()):
            raise LoreKitError("Session options must be boolean")
        if state["mode"] == "combat":
            raise LoreKitError("Do not change rules options during combat")
        state["options"].update(options)
        return {"type": "configured", "options": state["options"].copy()}
    if op == "creature_import":
        aid = intent.get("actor")
        if not isinstance(aid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", aid) or aid in state["actors"]:
            raise LoreKitError("Actor ID must be new and nonempty")
        created = creatures.build(intent.get("template"), intent.get("name"), bundle)
        created["character_id"] = intent["character_id"]
        state["actors"][aid] = created
        return {"type": "creature_imported", "actor": aid, "character_id": intent["character_id"]}
    if op == "character_create" or op == "character_import":
        aid = intent.get("actor")
        if not isinstance(aid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", aid) or aid in state["actors"]:
            raise LoreKitError("Actor ID must be new and nonempty")
        a = characters.build(intent.get("build"), bundle, imported=op == "character_import")
        a["character_id"] = intent.get("character_id")
        state["actors"][aid] = a
        return {"type": "character_created", "actor": aid, "character_id": a["character_id"]}
    if op == "creature_act":
        return creatures.act(state, intent, bundle, rng)
    if op == "signature_decide":
        return creatures.decide(state, intent, bundle, rng)
    if op == "panic_decide":
        return decisions.panic(state, intent, bundle, rng)
    if op == "burst_decide":
        return decisions.burst(state, intent, bundle)
    if op == "stealth_act":
        return complete_check(state, stealth.act(state, intent, bundle, rng), bundle, rng)
    if op == "stealth_phase":
        return stealth.phase(state, rng, bundle)
    if op == "detection_decide":
        return stealth.decide(state, intent, bundle, rng)
    if op == "tracker":
        return stealth.tracker(state, intent, bundle, rng)
    if op == "scene_setup":
        if state["combat"]:
            raise LoreKitError("End combat before replacing the scene")
        return scenes.setup(state, intent)
    if op == "combat_start":
        return scenes.start_combat(state, intent, rng)
    if op == "attack":
        return combat.declare(state, intent, bundle)
    if op == "defense_decide":
        return complete_check(state, combat.defend(state, intent, bundle, rng), bundle, rng)
    if op == "approach":
        return scenes.approach(state, intent.get("actor"), intent.get("target"))
    if op == "move":
        return scenes.move(state, intent.get("actor"), intent.get("zone"), bundle)
    if op == "end_turn":
        return scenes.end_turn(state, intent.get("actor"), rng, bundle)
    if op == "combat_end":
        if state["combat"] is None:
            raise LoreKitError("No active combat")
        state["combat"] = None
        state["mode"] = "stealth"
        return {"type": "combat_ended"}
    if op == "hold_off":
        return scenes.hold_off(state, intent.get("actor"), integer(intent.get("card"), "initiative card", 1, 10))
    if op == "recover":
        return complete_check(state, survival.recover(state, intent, bundle, rng), bundle, rng)
    if op == "time_advance":
        result = survival.advance_time(state, intent.get("unit"), intent.get("amount", 1))
        result["events"] = effects.drain(state, rng, bundle=bundle)
        return result
    if op == "hazard":
        return survival.hazard(state, intent)
    if op == "supply":
        return survival.supply(actor(state, intent.get("actor")), intent.get("item"), bundle, rng)
    if op == "damage":
        return {"events": survival.damage(state, intent.get("actor"), intent.get("amount"), bundle, rng)}
    if op == "check":
        if set(intent) & {"continuation", "no_stress", "no_push", "_base_dice"}:
            raise LoreKitError("Internal check parameters require a rules operation, not a generic check")
        return checks.start(state, intent, bundle, rng)
    if op == "check_decide":
        return complete_check(state, checks.decide(state, intent, bundle, rng), bundle, rng)
    if op == "story_award":
        a = actor(state, intent.get("actor"))
        if a["kind"] != "pc" or not isinstance(intent.get("reason"), str) or not intent["reason"].strip():
            raise LoreKitError("Story point award requires a PC and a GM reason")
        amount = integer(intent.get("amount", 1), "story point award", 1, 3)
        if a["story_points"] + amount > 3:
            raise LoreKitError("Story point cap is three")
        a["story_points"] += amount
        return {"type": "story_points", "actor": intent["actor"], "balance": a["story_points"]}
    if op == "stress":
        a = actor(state, intent.get("actor"))
        if not isinstance(intent.get("reason"), str) or not intent["reason"].strip():
            raise LoreKitError("Stress requires a concrete GM trigger")
        effects.stress(a, integer(intent.get("amount", 1), "stress amount"))
        return {"type": "stress", "actor": intent["actor"], "stress": a["stress"]}
    if op == "panic":
        effects.enqueue_panic(state, intent.get("actor"), intent.get("reason"))
        return {"events": effects.drain(state, rng), "pending": state["pending"]}
    if op == "queue_continue":
        return {"events": effects.drain(state, rng), "pending": state["pending"]}
    raise LoreKitError(f"Unsupported Alien operation: {op}")


def complete_check(state, result, bundle, rng):
    if result.get("type") == "check_complete" and result.get("continuation"):
        if result["continuation"]["kind"] == "stun":
            return survival.finish_stun(state, result)
        if result["continuation"]["kind"] == "attack":
            return combat.finish(state, result, bundle, rng)
        if result["continuation"]["kind"] == "recover":
            return survival.finish_recover(state, result, bundle, rng)
        if result["continuation"]["kind"] == "search":
            aid = result["continuation"]["actor"]
            a = actor(state, aid)
            found = [
                bid
                for bid, b in sorted(state["actors"].items())
                if bid != aid and b["zone"] == a["zone"] and b["hidden"]
            ][: result["successes"][0]]
            for bid in found:
                actor(state, bid)["hidden"] = False
                actor(state, bid)["revealed"] = True
            return {"type": "search_complete", "checks": result["checks"], "found": found}
        if result["continuation"]["kind"] == "due":
            resolved = survival.finish_due(state, result, bundle, rng)
            resolved["events"].extend(effects.drain(state, rng, bundle=bundle))
            return resolved
        raise LoreKitError("Unsupported check continuation")
    return result
