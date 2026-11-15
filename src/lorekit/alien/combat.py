"""Declared personal attacks and durable pre-roll defense decisions."""

from __future__ import annotations

import copy

from lorekit.alien import checks, effects, scenes, survival
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def declare(state, intent, bundle, *, forced=False):
    if state["mode"] != "combat":
        raise LoreKitError("Start combat first")
    aid, tid = intent.get("actor"), intent.get("target")
    a, target = actor(state, aid), actor(state, tid)
    if aid == tid or target["dead"]:
        raise LoreKitError("Choose another living target")
    weapon_id = intent.get("weapon")
    if weapon_id is None:
        definition = {"damage": 1, "modifier": 0, "minimum": 0, "maximum": 0}
        instance = None
    else:
        instance, definition = survival.item(a, weapon_id, bundle)
        if not instance.get("equipped") or instance.get("dropped") or instance.get("jammed"):
            raise LoreKitError("Weapon must be available, equipped and unjammed")
        if "damage" not in definition:
            raise LoreKitError("Item is not a supported weapon")
        if definition.get("supply_type") and not instance["supply"]:
            raise LoreKitError("Weapon supply is empty; reload first")
    if a.get("grappled_by"):
        raise LoreKitError("Escape the grapple before attacking")
    band = scenes.range_band(state, aid, tid)
    if band > definition.get("maximum", 0):
        raise LoreKitError("Target beyond weapon maximum range")
    if not scenes.line_of_sight(state, aid, tid):
        if target.get("cover") != "full" or intent.get("known_target") is not True:
            raise LoreKitError("Target not visible; covered known target requires explicit GM confirmation")
    unaware = tid in a.get("ambush_targets", []) or bool(target.get("grappled_by"))
    modifier = definition.get("modifier", 0) - (0 if unaware else 2 * max(0, definition.get("minimum", 0) - band))
    if unaware:
        modifier += 2
    if intent.get("weak_spot", False):
        modifier -= 2
    if intent.get("aim", False):
        if band == 0:
            raise LoreKitError("Cannot aim at Adjacent range")
        scenes.spend(state, aid, "quick")
        modifier += 2
    if definition.get("aim_required") and not intent.get("aim"):
        raise LoreKitError("This weapon requires aim")
    if target.get("cover") == "partial":
        modifier -= 2
    if target.get("cover") == "full":
        modifier -= 3
    special = intent.get("special")
    if special not in (None, "disarm", "pass", "shove", "grapple"):
        raise LoreKitError("Unknown close-combat special attack")
    if special and band != 0:
        raise LoreKitError("Special attack requires Adjacent range")
    if special:
        modifier -= definition.get("modifier", 0)
    if intent.get("full_auto") and not definition.get("full_auto"):
        raise LoreKitError("Weapon cannot fire full auto")
    if not forced:
        scenes.spend(state, aid, "full")
    a.pop("ambush_targets", None)
    attack = {
        "unaware": unaware,
        "actor": aid,
        "target": tid,
        "weapon": weapon_id,
        "definition": copy.deepcopy(definition),
        "skill": "close_combat" if band == 0 else "ranged_combat",
        "modifier": modifier,
        "special": special,
        "weak_spot": intent.get("weak_spot", False),
        "conserve": intent.get("conserve", False),
        "full_auto": intent.get("full_auto", False),
        "burst": 1,
        "aim": intent.get("aim", False),
        "band": band,
    }
    state["pending"] = {"type": "defense", "id": f"defense:{state['revision'] + 1}", "attack": attack}
    return {"type": "defense_required", "decision_id": state["pending"]["id"], "target": tid}


def defend(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "defense" or p["id"] != intent.get("decision_id"):
        raise LoreKitError("No matching defense decision")
    attack = p["attack"]
    tid = attack["target"]
    if intent.get("actor") != tid:
        raise LoreKitError("Target must decide defense")
    target = actor(state, tid)
    choice = intent.get("choice")
    if choice not in ("defend", "dodge", "decline"):
        raise LoreKitError("Choose defend, dodge or decline")
    spec = {
        "actor": attack["actor"],
        "skill": attack["skill"],
        "modifier": attack["modifier"],
        "continuation": {"kind": "attack", "attack": attack},
    }
    if "fixed_dice" in attack:
        spec["_base_dice"] = attack["fixed_dice"]
    if choice != "decline":
        expected = "defend" if attack["band"] == 0 else "dodge"
        if (
            attack.get("unaware")
            or target["species"] == "xenomorph"
            or choice != expected
            or target.get("cover") == "full"
            or target["health"] == 0
        ):
            raise LoreKitError("This defense is unavailable")
        scenes.spend(state, tid, "quick", out_of_turn=True)
        spec["opponent"] = {"actor": tid, "skill": "close_combat" if choice == "defend" else "mobility"}
    state["pending"] = None
    return checks.start(state, spec, bundle, rng, queued=True)


def finish(state, result, bundle, rng):
    attack = result["continuation"]["attack"]
    a, target = actor(state, attack["actor"]), actor(state, attack["target"])
    definition = attack["definition"]
    net = result["successes"][0] - (result["successes"][1] if len(result["successes"]) == 2 else 0)
    events = [
        {
            "type": "attack_result",
            "actor": attack["actor"],
            "target": attack["target"],
            "net_successes": net,
            "checks": result["checks"],
        }
    ]
    conserve = False
    if net > 0:
        special = attack["special"]
        if special == "grapple":
            target["grappled_by"] = attack["actor"]
            a["grappling"] = attack["target"]
        elif special == "shove":
            a["adjacent"] = [x for x in a.get("adjacent", []) if x != attack["target"]]
            target["adjacent"] = [x for x in target.get("adjacent", []) if x != attack["actor"]]
        elif special in ("disarm", "pass"):
            state["pending"] = {"type": "special_choice", "attack": attack}
        else:
            conserve = attack["conserve"] and net > 1 and not attack["full_auto"] and not definition.get("single_shot")
            amount = definition["damage"] + net - 1 - int(conserve)
            if definition.get("stun"):
                stun_result = survival.stun(state, attack["target"], amount, bundle, rng)
                if stun_result.get("type") == "check_complete":
                    stun_result = survival.finish_stun(state, stun_result)
                events.append(stun_result)
            else:
                events.extend(
                    survival.damage(
                        state,
                        attack["target"],
                        amount,
                        bundle,
                        rng,
                        piercing=definition.get("piercing", False),
                        weak_spot=attack["weak_spot"],
                        base_damage=definition["damage"],
                        barrier_armor=target.get("barrier_armor", 0) if target.get("cover") == "full" else 0,
                    )
                )
    else:
        conserve = False
    if attack["weapon"]:
        instance, definition = survival.item(a, attack["weapon"], bundle)
        if definition.get("single_shot"):
            instance["supply"] = 0
        elif definition.get("supply_type") and not (net > 0 and conserve):
            events.append(survival.supply(a, attack["weapon"], bundle, rng))
    # Full-auto followups are explicit decisions. No silent extra attacks.
    pushed = result["checks"][0]["pushes"] > 0
    if state["pending"] is None and attack["full_auto"] and net > 0 and not pushed and attack["burst"] < 3:
        state["pending"] = {"type": "burst", "attack": attack}
    if "signature_execution" in attack:
        from lorekit.alien.creatures import resume

        execution = attack["signature_execution"]
        execution["hit"] = net > 0
        execution["damage"] = any(e.get("type") == "damage" and e.get("inflicted", 0) > 0 for e in events)
        state["pending"] = {"type": "signature", "execution": execution}
        continuation = resume(state, bundle, rng)
        continuation["events"] = events + continuation.get("events", [])
        return continuation
    events.extend(effects.drain(state, rng))
    return {"type": "attack_complete", "events": events, "pending": copy.deepcopy(state["pending"])}
