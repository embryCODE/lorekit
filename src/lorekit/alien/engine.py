"""Alien command router. Unsupported operations fail before canonical state writes."""

from __future__ import annotations

import copy
import re

from lorekit.alien import (
    actions,
    area,
    characters,
    checks,
    combat,
    content,
    creatures,
    decisions,
    effects,
    environment,
    forced_movement,
    initiative,
    intents,
    scenes,
    stealth,
    survival,
    talent_actions,
    trauma,
)
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def transition(state: dict, intent: dict, rng) -> dict:
    intents.validate(intent)
    op = intent.get("op")
    if state["pending"] is not None and op not in (
        "check_decide",
        "panic_decide",
        "defense_decide",
        "burst_decide",
        "detection_decide",
        "signature_decide",
        "trauma_decide",
        "talent_answer",
        "initiative_decide",
        "special_decide",
        "hit_decide",
        "injury_decide",
        "overkill_decide",
        "area_defense",
        "creature_escape_decide",
        "search_decide",
        "trauma_due_decide",
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
        "trauma_decide",
        "talent_answer",
        "initiative_decide",
        "special_decide",
        "hit_decide",
        "injury_decide",
        "overkill_decide",
        "area_defense",
        "creature_escape_decide",
        "search_decide",
        "trauma_due_decide",
        "override",
    ):
        raise LoreKitError("Resolve the response queue before another operation")
    bundle = content.read(state["content_hash"])
    entry_ops = {
        "begin_turn",
        "end_turn",
        "attack",
        "action",
        "move",
        "approach",
        "creature_act",
        "creature_move",
        "recover",
        "check",
        "talent_use",
        "environment",
    }
    aid = intent.get("actor")
    if state["combat"] and op in entry_ops and aid in state["combat"]["slots"][state["combat"]["index"]]["actors"]:
        combat_state = state["combat"]
        token = f"{combat_state['round']}:{combat_state['index']}"
        entries = combat_state["slots"][combat_state["index"]]["actors"] if op == "end_turn" else [aid]
        fresh = [current for current in entries if actor(state, current).get("entered_turn") != token]
        if fresh:
            if op != "begin_turn":
                preview = copy.deepcopy(state)
                for current in fresh:
                    candidate = actor(preview, current)
                    candidate["entered_turn"] = token
                    candidate["stunned"] = False
                    candidate.pop("skip_turn", None)
                    candidate.pop("escape_required", None)
                    candidate["panic_responses"] = [v for v in candidate["panic_responses"] if v < 10]
                    if candidate["species"] == "human" and not candidate["dead"]:
                        candidate["health"] = max(1, candidate["health"])
                transition(preview, copy.deepcopy(intent), copy.deepcopy(rng))
            for current in fresh:
                actor(state, current)["entered_turn"] = token
                scenes.prepare_turn(state, current)
            if state["queue"]:
                if op != "begin_turn":
                    state["queue"].append({"type": "resume_intent", "intent": copy.deepcopy(intent)})
                return {"type": "turn_effects", "actor": aid, "events": effects.drain(state, rng, bundle=bundle)}
    if op == "begin_turn":
        if not state["combat"] or aid not in state["combat"]["slots"][state["combat"]["index"]]["actors"]:
            raise LoreKitError("Only the current actor may begin a turn")
        return {"type": "turn_begun", "actor": aid}
    guarded = environment.pressure_guard(state, intent, bundle, rng)
    if guarded is not None:
        return complete_check(state, guarded, bundle, rng)
    if op == "disclose":
        a = actor(state, intent.get("actor"))
        fields = intent.get("fields")
        if (
            not isinstance(fields, list)
            or len(set(fields)) != len(fields)
            or any(field not in {"health", "health_max", "stress", "story_points", "species"} for field in fields)
            or not isinstance(intent.get("reason"), str)
            or not intent["reason"].strip()
        ):
            raise LoreKitError("Choose public mechanical fields and record a GM disclosure reason")
        if a.get("secret_identity") and "species" in fields:
            raise LoreKitError("Use the synthetic revelation horror trigger before disclosing species")
        a["public_fields"] = fields
        return {"type": "facts_disclosed", "actor": intent["actor"], "fields": fields, "reason": intent["reason"]}
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
            if "armor" in changes and target.get("armor_item"):
                next(i for i in target["inventory"] if i["id"] == target["armor_item"])["armor_current"] = changes[
                    "armor"
                ]
            if "zone" in changes and changes["zone"] != target["zone"]:
                aid = intent["actor"]
                for other in state["actors"].values():
                    if aid in other.get("adjacent", []):
                        other["adjacent"].remove(aid)
                target["adjacent"] = []
                for order in state["scene"].get("crawl_order", {}).values():
                    if aid in order:
                        order.remove(aid)
                if state["scene"]["zones"][changes["zone"]].get("crawlspace"):
                    state["scene"].setdefault("crawl_order", {}).setdefault(changes["zone"], []).append(aid)
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
        if options.get("mental_trauma") and set(bundle["collections"].get("traumas", {})) != {
            str(t * 10 + u) for t in range(1, 7) for u in range(1, 7)
        }:
            raise LoreKitError("Mental trauma requires a complete local D66 table")
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
    if op == "talent_use":
        return complete_check(state, talent_actions.act(state, intent, bundle, rng), bundle, rng)
    if op == "talent_answer":
        return talent_actions.answer(state, intent)
    if op == "trauma_due_decide":
        result = trauma.due_decide(state, intent)
        result["events"] = effects.drain(state, rng, bundle=bundle)
        return result
    if op == "trauma_event":
        result = trauma.event(state, intent)
        result["events"] = effects.drain(state, rng, bundle=bundle)
        return result
    if op == "trauma":
        return complete_check(state, trauma.start(state, intent, bundle, rng), bundle, rng)
    if op == "trauma_decide":
        return trauma.decide(state, intent, bundle, rng)
    if op == "environment":
        return complete_check(state, environment.act(state, intent, bundle, rng), bundle, rng)
    if op == "action":
        return complete_check(state, actions.act(state, intent, bundle, rng), bundle, rng)
    if op == "facehugger_remove":
        return complete_check(state, creatures.remove_facehugger(state, intent, bundle, rng), bundle, rng)
    if op == "egg_release":
        if not isinstance(intent.get("reason"), str) or not intent["reason"].strip():
            raise LoreKitError("Explicit egg disturbance requires a GM reason")
        result = creatures.release_egg(state, intent.get("actor"), intent.get("target"), bundle, rng)
        result["events"] = effects.drain(state, rng, bundle=bundle)
        return result
    if op == "lifecycle":
        return creatures.lifecycle(state, intent, bundle, rng)
    if op == "creature_move":
        return creatures.move_path(state, intent, bundle, rng)
    if op == "creature_escape_decide":
        return creatures.escape_decide(state, intent, bundle, rng)
    if op == "creature_act":
        return creatures.act(state, intent, bundle, rng)
    if op == "signature_decide":
        return creatures.decide(state, intent, bundle, rng)
    if op == "overkill_decide":
        return decisions.overkill(state, intent, bundle, rng)
    if op == "panic_decide":
        return decisions.panic(state, intent, bundle, rng)
    if op == "burst_decide":
        return decisions.burst(state, intent, bundle)
    if op == "search_decide":
        return stealth.search_decide(state, intent, bundle, rng)
    if op == "scene_mode":
        if state["combat"] or not state["scene"] or intent.get("mode") not in ("stealth", "social"):
            raise LoreKitError("Choose social/stealth mode outside active combat")
        state["mode"] = intent["mode"]
        state["scene"]["phase"] = "pc"
        state["scene"]["acted"] = []
        return {"type": "mode_changed", "mode": intent["mode"]}
    if op == "stealth_act":
        return complete_check(state, stealth.act(state, intent, bundle, rng), bundle, rng)
    if op == "leave_combat":
        return stealth.leave(state, intent, bundle, rng)
    if op == "stealth_phase":
        result = stealth.phase(state, rng, bundle, elapsed=intent.get("elapsed", False))
        result["events"] = effects.drain(state, rng, bundle=bundle)
        return result
    if op == "detection_decide":
        return stealth.decide(state, intent, bundle, rng)
    if op == "tracker":
        return stealth.tracker(state, intent, bundle, rng)
    if op == "scene_setup":
        if state["combat"]:
            raise LoreKitError("End combat before replacing the scene")
        return scenes.setup(state, intent)
    if op == "initiative_decide":
        result = initiative.decide(state, intent, bundle, rng)
        if state["pending"] is None:
            result["events"] = effects.drain(state, rng, bundle=bundle)
        return result
    if op == "combat_start":
        result = scenes.start_combat(state, intent, rng, bundle)
        result["events"] = creatures.egg_round(state, bundle, rng)
        result["events"].extend(effects.drain(state, rng, bundle=bundle))
        return result
    if op == "area_defense":
        return complete_check(state, area.decide(state, intent, bundle, rng), bundle, rng)
    if op == "attack":
        return combat.declare(state, intent, bundle)
    if op == "hit_decide":
        return combat.hit_decide(state, intent, bundle, rng)
    if op == "injury_decide":
        return survival.injury_decide(state, intent, bundle, rng)
    if op == "special_decide":
        return combat.special_decide(state, intent, bundle, rng)
    if op == "defense_decide":
        return complete_check(state, combat.defend(state, intent, bundle, rng), bundle, rng)
    if op == "approach":
        a = actor(state, intent.get("actor"))
        if (
            state["scene"]
            and state["scene"]["zones"][a["zone"]].get("zero_g")
            and not any(e["hook"] == "zero_g_immunity" for e in characters.talents(a, bundle))
        ):
            if actor(state, intent.get("target"))["zone"] != a["zone"]:
                raise LoreKitError("Approach requires a target in the same zone")
            scenes.spend(state, intent["actor"], scenes.movement_cost(a))
            return complete_check(
                state,
                checks.start(
                    state,
                    {
                        "actor": intent["actor"],
                        "skill": "mobility",
                        "_zero_g_move": True,
                        "continuation": {
                            "kind": "zero_g_approach",
                            "actor": intent["actor"],
                            "target": intent["target"],
                        },
                    },
                    bundle,
                    rng,
                ),
                bundle,
                rng,
            )
        result = scenes.approach(state, intent.get("actor"), intent.get("target"), bundle)
        creatures.egg_adjacent(state, intent.get("actor"))
        result["events"] = effects.drain(state, rng, bundle=bundle)
        return result
    if op == "move":
        a = actor(state, intent.get("actor"))
        from lorekit.alien.characters import talents

        if (
            state["scene"]
            and (
                state["scene"]["zones"][a["zone"]].get("zero_g")
                or state["scene"]["zones"].get(intent.get("zone"), {}).get("zero_g")
            )
            and not any(e["hook"] == "zero_g_immunity" for e in talents(a, bundle))
        ):
            scenes.route(state, intent["actor"], intent.get("zone"))
            scenes.spend(state, intent["actor"], scenes.movement_cost(a))
            return complete_check(
                state,
                checks.start(
                    state,
                    {
                        "actor": intent["actor"],
                        "skill": "mobility",
                        "_zero_g_move": True,
                        "continuation": {"kind": "zero_g_move", "actor": intent["actor"], "zone": intent["zone"]},
                    },
                    bundle,
                    rng,
                ),
                bundle,
                rng,
            )
        return scenes.move(state, intent.get("actor"), intent.get("zone"), bundle, rng=rng)
    if op == "end_turn":
        return scenes.end_turn(state, intent.get("actor"), rng, bundle)
    if op == "combat_end":
        if state["combat"] is None:
            raise LoreKitError("No active combat")
        scenes.finish_combat(state)
        return {"type": "combat_ended"}
    if op == "hold_off":
        return scenes.hold_off(state, intent.get("actor"), integer(intent.get("card"), "initiative card", 1, 10))
    if op == "recover":
        return complete_check(state, survival.recover(state, intent, bundle, rng), bundle, rng)
    if op == "time_advance":
        result = survival.advance_time(state, intent.get("unit"), intent.get("amount", 1))
        result["events"] = []
        if intent.get("unit") == "round":
            from lorekit.alien import acid

            for current_id, current in state["actors"].items():
                if not current["dead"] and current.get("acid"):
                    result["events"].extend(acid.tick(state, current_id, bundle, rng))
            result["events"].extend(acid.tick_floors(state, rng))
            result["events"].extend(environment.round_start(state, bundle, rng))
            result["events"].extend(creatures.egg_round(state, bundle, rng))
        result["events"].extend(effects.drain(state, rng, bundle=bundle))
        return result
    if op == "hazard":
        return complete_check(state, survival.hazard(state, intent, bundle, rng), bundle, rng)
    if op == "hazard_remedy":
        return survival.remedy(state, intent)
    if op == "supply":
        result = survival.supply(actor(state, intent.get("actor")), intent.get("item"), bundle, rng)
        survival.sync_air(state, intent["actor"])
        return result
    if op == "damage":
        events = survival.damage(state, intent.get("actor"), intent.get("amount"), bundle, rng)
        events.extend(effects.drain(state, rng, bundle=bundle))
        return {"events": events, "reason": intent.get("reason", "Explicit GM damage")}
    if op == "check":
        if set(intent) & {"continuation", "no_stress", "no_push"} or any(str(key).startswith("_") for key in intent):
            raise LoreKitError("Internal check parameters require a rules operation, not a generic check")
        return checks.start(state, intent, bundle, rng)
    if op == "check_benefits":
        return checks.benefits(state, intent)
    if op == "check_decide":
        return complete_check(state, checks.decide(state, intent, bundle, rng), bundle, rng)
    if op == "story_award":
        a = actor(state, intent.get("actor"))
        if a["kind"] != "pc" or not isinstance(intent.get("reason"), str) or not intent["reason"].strip():
            raise LoreKitError("Story point award requires a PC and a GM reason")
        amount = integer(intent.get("amount", 1), "story point award", 1, 3)
        if a["story_points"] + amount > 3:
            raise LoreKitError("Story point cap is three")
        act_id = intent.get("act_id")
        if not isinstance(act_id, str) or not act_id.strip():
            raise LoreKitError("Story award requires an act_id")
        if act_id in a.get("story_awards", []):
            raise LoreKitError("Story point already awarded for this act")
        a.setdefault("story_awards", []).append(act_id)
        a["story_points"] += amount
        return {"type": "story_points", "actor": intent["actor"], "balance": a["story_points"]}
    if op == "horror":
        result = effects.horror(state, intent)
        result["events"] = effects.drain(state, rng, bundle=bundle)
        return result
    if op == "stress":
        a = actor(state, intent.get("actor"))
        if not isinstance(intent.get("reason"), str) or not intent["reason"].strip():
            raise LoreKitError("Stress requires a concrete GM trigger")
        effects.stress(a, integer(intent.get("amount", 1), "stress amount"))
        return {"type": "stress", "actor": intent["actor"], "stress": a["stress"]}
    if op == "panic":
        effects.enqueue_panic(state, intent.get("actor"), intent.get("reason"))
        return {"events": effects.drain(state, rng, bundle=bundle), "pending": state["pending"]}
    if op == "queue_continue":
        return {"events": effects.drain(state, rng, bundle=bundle), "pending": state["pending"]}
    raise LoreKitError(f"Unsupported Alien operation: {op}")


def complete_check(state, result, bundle, rng):
    if result.get("type") == "check_complete" and result.get("continuation"):
        if result["continuation"]["kind"] == "forced_move":
            return complete_check(state, forced_movement.resolve(state, result, bundle, rng), bundle, rng)
        if result["continuation"]["kind"] == "shoot_hull":
            return environment.finish_hull_shot(state, result, bundle, rng)
        if result["continuation"]["kind"] == "pressure":
            return environment.finish_pressure(state, result, bundle, rng)
        if result["continuation"]["kind"] == "facehugger_removal":
            return creatures.finish_removal(state, result)
        if result["continuation"]["kind"] == "egg_disturbance":
            continuation = result["continuation"]
            events = []
            if not result["success"]:
                events.append(creatures.release_egg(state, continuation["egg"], continuation["actor"], bundle, rng))
            events.extend(effects.drain(state, rng, bundle=bundle))
            return {
                "type": "egg_disturbance",
                "success": result["success"],
                "checks": result["checks"],
                "events": events,
            }
        if result["continuation"]["kind"] == "zero_g_approach":
            c = result["continuation"]
            if not result["success"]:
                effects.stress(actor(state, c["actor"]), 1)
                return {"type": "zero_g_spin", "actor": c["actor"]}
            return scenes.approach(state, c["actor"], c["target"], bundle, paid=True)
        if result["continuation"]["kind"] == "zero_g_move":
            aid = result["continuation"]["actor"]
            if result["success"]:
                return scenes.move(
                    state, aid, result["continuation"]["zone"], bundle, forced=True, rng=rng, zero_g_checked=True
                )
            effects.stress(actor(state, aid), 1)
            return {"type": "zero_g_spin", "actor": aid, "checks": result["checks"]}
        if result["continuation"]["kind"] == "talent":
            return talent_actions.finish(state, result)
        if result["continuation"]["kind"] == "trauma":
            return trauma.finish(state, result, bundle, rng)
        if result["continuation"]["kind"] == "fall":
            aid = result["continuation"]["actor"]
            amount = max(0, result["continuation"]["damage"] - result["successes"][0])
            return {
                "type": "fall",
                "checks": result["checks"],
                "events": survival.damage(state, aid, amount, bundle, rng, bypass=True),
            }
        if result["continuation"]["kind"] == "extinguish":
            if result["success"]:
                actor(state, result["continuation"]["target"]).pop("burning", None)
            return {"type": "extinguish", "success": result["success"], "checks": result["checks"]}
        if result["continuation"]["kind"] == "signature_resist":
            execution = result["continuation"]["execution"]
            events = []
            if not result["success"]:
                events.extend(
                    survival.damage(
                        state, execution["target"], result["continuation"]["amount"], bundle, rng, bypass=True
                    )
                )
            state["pending"] = {"type": "signature", "execution": execution}
            resolved = creatures.resume(state, bundle, rng)
            resolved["events"] = events + resolved.get("events", [])
            return resolved
        if result["continuation"]["kind"] == "disease_exposure":
            return survival.finish_exposure(state, result)
        if result["continuation"]["kind"] == "action":
            resolved = actions.finish(state, result, bundle, rng)
            resolved["events"] = resolved.get("events", []) + effects.drain(state, rng, bundle=bundle)
            return resolved
        if result["continuation"]["kind"] == "stun":
            return survival.finish_stun(state, result)
        if result["continuation"]["kind"] == "area":
            return area.finish(state, result, bundle, rng)
        if result["continuation"]["kind"] == "quick_shot":
            return combat.finish_quick_shot(state, result, bundle, rng)
        if result["continuation"]["kind"] == "personal_safety":
            return combat.finish_safety(state, result)
        if result["continuation"]["kind"] == "attack":
            return combat.finish(state, result, bundle, rng)
        if result["continuation"]["kind"] == "recover":
            return survival.finish_recover(state, result, bundle, rng)
        if result["continuation"]["kind"] == "search":
            return stealth.search_finish(state, result)
        if result["continuation"]["kind"] == "due":
            resolved = survival.finish_due(state, result, bundle, rng)
            resolved["events"].extend(effects.drain(state, rng, bundle=bundle))
            return resolved
        raise LoreKitError("Unsupported check continuation")
    return result
