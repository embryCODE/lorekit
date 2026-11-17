"""Declared personal attacks and durable pre-roll defense decisions."""

from __future__ import annotations

import copy

from lorekit.alien import checks, effects, scenes, survival
from lorekit.alien.characters import incapacitated, talents
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def declare(state, intent, bundle, *, forced=False):
    if state["mode"] != "combat":
        raise LoreKitError("Start combat first")
    aid, tid = intent.get("actor"), intent.get("target")
    a, target = actor(state, aid), actor(state, tid)
    effects.refresh_overkill(state, aid)
    if a["species"] == "xenomorph":
        raise LoreKitError("Use creature_act and a supported signature instead of a human attack")
    if a.get("escaped"):
        raise LoreKitError("Re-enter combat before attacking")
    if aid == tid or target["dead"]:
        raise LoreKitError("Choose another living target")
    if a.get("overkill") and tid not in a["overkill"]:
        raise LoreKitError("Overkill must attack a declared enemy")
    weapon_id = intent.get("weapon")
    if weapon_id is None:
        definition = {"damage": 1, "modifier": 0, "minimum": 0, "maximum": 0}
        instance = None
    else:
        instance, definition = survival.item(a, weapon_id, bundle)
        if not instance.get("equipped") or instance.get("dropped") or instance.get("jammed"):
            raise LoreKitError("Weapon must be available, equipped and unjammed")
        if 3 in a.get("system_damage", []) and definition.get("hands", 1) > 1:
            raise LoreKitError("Damaged arm servos allow only one-handed gear")
        if "damage" not in definition:
            raise LoreKitError("Item is not a supported weapon")
        if definition.get("supply_type") and not instance["supply"]:
            raise LoreKitError("Weapon supply is empty; reload first")
    if a.get("grappled_by"):
        raise LoreKitError("Escape the grapple before attacking")
    if a.get("grappling") and (a["grappling"] != tid or weapon_id is not None):
        raise LoreKitError("Grappler can only make an unarmed attack on its held target")
    band = scenes.range_band(state, aid, tid)
    if band > definition.get("maximum", 0):
        raise LoreKitError("Target beyond weapon maximum range")
    if not scenes.line_of_sight(state, aid, tid):
        if target.get("cover") != "full" or intent.get("known_target") is not True:
            raise LoreKitError("Target not visible; covered known target requires explicit GM confirmation")
    unaware = tid in a.get("ambush_targets", []) or bool(target.get("grappled_by")) or bool(target.get("unconscious"))
    modifier = definition.get("modifier", 0) - (0 if unaware else 2 * max(0, definition.get("minimum", 0) - band))
    if band > 0:
        modifier += {"small": -2, "normal": 0, "large": 2}[target.get("size", "normal")]
    if unaware and (band == 0 or tid in a.get("ambush_targets", [])):
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
    if special and intent.get("full_auto"):
        raise LoreKitError("Special attacks do not fire bursts")
    if intent.get("full_auto") and not definition.get("full_auto"):
        raise LoreKitError("Weapon cannot fire full auto")
    hooks = talents(a, bundle)
    quick = intent.get("quick_attack", False)
    martial = weapon_id is None and any(e["hook"] == "martial_arts" for e in hooks)
    if quick:
        rapid = definition.get("category") == "pistol" and any(e["hook"] == "rapid_fire" for e in hooks)
        if not (martial or rapid):
            raise LoreKitError("Quick attack requires a supported unarmed/pistol talent")
        modifier -= 2
    if intent.get("haymaker"):
        if band != 0 or not any(e["hook"] == "haymaker" for e in hooks) or quick:
            raise LoreKitError("Haymaker requires a full close attack and its talent")
        scenes.spend(state, aid, "quick")
        modifier += 2
    for effect in hooks:
        if effect["hook"] == "weapon_modifier" and effect["weapon"] == (
            instance["content_id"] if instance else "unarmed"
        ):
            modifier += effect["value"]
    if definition.get("explosive"):
        borders = [edge for edge in state["scene"]["borders"] if target["zone"] in (edge["a"], edge["b"])]
        if all(not edge.get("open") or edge.get("sealed") or edge.get("locked") for edge in borders):
            modifier += 2
    if not forced:
        scenes.spend(state, aid, "quick" if quick else "full", grapple_attack=bool(a.get("grappling")))
    a.pop("ambush_targets", None)
    if a.get("overkill"):
        a["overkill_attacked"] = True
    attack = {
        "unaware": unaware,
        "known_target": intent.get("known_target", False),
        "shove_zone": intent.get("shove_zone"),
        "order": intent.get("order"),
        "actor": aid,
        "target": tid,
        "weapon": weapon_id,
        "definition": copy.deepcopy(definition),
        "skill": "close_combat" if band == 0 else "ranged_combat",
        "martial": martial,
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
    effects.refresh_overkill(state, tid)
    choice = intent.get("choice")
    if choice == "quick_shot":
        if attack["band"] == 0 or not any(e["hook"] == "quick_shot" for e in talents(target, bundle)):
            raise LoreKitError("Quick Shot requires its talent and an incoming ranged attack")
        scenes.spend(state, tid, "quick", out_of_turn=True)
        effects.stress(target, 1)
        state["pending"] = None
        declare(state, {"actor": tid, "target": attack["actor"], "weapon": intent.get("weapon")}, bundle, forced=True)
        reaction = state["pending"]["attack"]
        if reaction["band"] == 0 or not reaction["weapon"]:
            raise LoreKitError("Quick Shot requires a ranged weapon")
        state["pending"] = None
        spec = {
            "actor": attack["actor"],
            "skill": attack["skill"],
            "modifier": attack["modifier"],
            "two_hands": attack["definition"].get("hands", 1) > 1,
            "two_arms": attack["definition"].get("hands", 1) > 1,
            "opponent": {"actor": tid, "skill": reaction["skill"], "modifier": reaction["modifier"]},
            "continuation": {"kind": "quick_shot", "attacks": [copy.deepcopy(attack), copy.deepcopy(reaction)]},
        }
        if attack["definition"].get("explosive") or reaction["definition"].get("explosive"):
            from lorekit.alien import area

            result = area.begin(state, spec, attack)
            if not state["pending"]["targets"]:
                return area.decide(state, {}, bundle, rng)
            return result
        return checks.start(state, spec, bundle, rng, queued=True)
    if choice == "personal_safety":
        if not any(e["hook"] == "personal_safety" for e in talents(target, bundle)) or tid in attack.get(
            "redirected", []
        ):
            raise LoreKitError("Personal Safety is unavailable for this target")
        replacement = intent.get("target")
        other = actor(state, replacement)
        if (
            replacement in (tid, attack["actor"])
            or other["dead"]
            or intent.get("friendly") is not True
            or scenes.range_band(state, tid, replacement) > 1
        ):
            raise LoreKitError("Choose a friendly PC/NPC within Short range")
        effects.stress(target, 1)
        state["pending"] = None
        return checks.start(
            state,
            {
                "actor": tid,
                "skill": "manipulation",
                "continuation": {"kind": "personal_safety", "attack": copy.deepcopy(attack), "target": replacement},
            },
            bundle,
            rng,
            queued=True,
        )
    if choice not in ("defend", "dodge", "decline"):
        raise LoreKitError("Choose defend, dodge or decline")
    spec = {
        "actor": attack["actor"],
        "skill": attack["skill"],
        "modifier": attack["modifier"],
        "order": attack.get("order"),
        "context": attack["special"] or "attack",
        "continuation": {"kind": "attack", "attack": attack},
    }
    if attack.get("martial"):
        spec["attribute"] = "agility"
    if "fixed_dice" in attack:
        spec["_base_dice"] = attack["fixed_dice"]
    if choice != "decline":
        expected = "defend" if attack["band"] == 0 else "dodge"
        if (
            target.get("overkill")
            or attack.get("unaware")
            or target["species"] == "xenomorph"
            or choice != expected
            or target.get("cover") == "full"
            or incapacitated(target)
        ):
            raise LoreKitError("This defense is unavailable")
        scenes.spend(state, tid, "quick", out_of_turn=True)
        spec["opponent"] = {
            "actor": tid,
            "skill": "close_combat" if choice == "defend" else "mobility",
            "context": choice,
        }
        if (
            choice == "defend"
            and intent.get("unarmed") is True
            and any(e["hook"] == "martial_arts" for e in talents(target, bundle))
        ):
            spec["opponent"]["attribute"] = "agility"
    state["pending"] = None
    if attack["definition"].get("explosive"):
        from lorekit.alien import area

        result = area.begin(state, spec, attack)
        if not state["pending"]["targets"]:
            return area.decide(state, {}, bundle, rng)
        return result
    return checks.start(state, spec, bundle, rng, queued=True)


def finish(state, result, bundle, rng, *, reactions=True):
    attack = result["continuation"]["attack"]
    a, target = actor(state, attack["actor"]), actor(state, attack["target"])
    definition = attack["definition"]
    net = result["successes"][0] - (result["successes"][1] if len(result["successes"]) == 2 else 0)
    if net > 0 and reactions:
        candidates = [
            bid
            for bid, bodyguard in sorted(state["actors"].items())
            if bid not in (attack["actor"], attack["target"])
            and not incapacitated(bodyguard)
            and bodyguard["zone"] == target["zone"]
            and any(e["hook"] == "bodyguard" for e in talents(bodyguard, bundle))
        ]
        if candidates:
            state["pending"] = {
                "type": "hit_reaction",
                "attack": copy.deepcopy(attack),
                "result": copy.deepcopy(result),
                "candidates": candidates,
            }
            return {"type": "hit_reaction", "pending": copy.deepcopy(state["pending"])}
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
            if attack.get("shove_zone") is not None:
                events.append(scenes.move(state, attack["target"], attack["shove_zone"], bundle, forced=True, rng=rng))
            a["adjacent"] = [x for x in a.get("adjacent", []) if x != attack["target"]]
            target["adjacent"] = [x for x in target.get("adjacent", []) if x != attack["actor"]]
        elif special in ("disarm", "pass"):
            state["pending"] = {"type": "special_choice", "attack": attack}
        else:
            conserve = attack["conserve"] and net > 1 and not attack["full_auto"] and not definition.get("single_shot")
            amount = (
                definition["damage"]
                if definition.get("fixed_damage")
                else definition["damage"] + net - 1 - int(conserve)
            )
            if definition.get("stun"):
                barrier = (
                    target.get("barrier_armor", 0)
                    if (target.get("cover") == "full" or attack.get("area_secondary") and target.get("cover"))
                    and not attack.get("intercepted")
                    else 0
                )
                stun_result = survival.stun(
                    state,
                    attack["target"],
                    amount,
                    bundle,
                    rng,
                    piercing=definition.get("piercing", False),
                    weak_spot=attack["weak_spot"],
                    barrier_armor=barrier,
                    base_damage=definition["damage"],
                )
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
                        barrier_armor=target.get("barrier_armor", 0)
                        if (target.get("cover") == "full" or attack.get("area_secondary") and target.get("cover"))
                        and not attack.get("intercepted")
                        else 0,
                    )
                )
    else:
        conserve = False
        if attack["skill"] == "ranged_combat" and not definition.get("explosive") and not attack.get("area_secondary"):
            candidates = sorted(
                tid for tid in target.get("adjacent", []) if tid != attack["actor"] and not actor(state, tid)["dead"]
            )
            if candidates:
                unintended = rng.choice(candidates)
                dice = [rng.randint(1, 6), rng.randint(1, 6)]
                hits = dice.count(6)
                event = {"type": "friendly_fire", "target": unintended, "dice": dice, "events": []}
                if hits:
                    victim = actor(state, unintended)
                    barrier = victim.get("barrier_armor", 0) if victim.get("cover") == "full" else 0
                    if definition.get("stun"):
                        resolved = survival.stun(
                            state,
                            unintended,
                            definition["damage"] + hits - 1,
                            bundle,
                            rng,
                            piercing=definition.get("piercing", False),
                            base_damage=definition["damage"],
                            barrier_armor=barrier,
                        )
                        if resolved.get("type") == "check_complete":
                            resolved = survival.finish_stun(state, resolved)
                        event["events"] = [resolved]
                    else:
                        event["events"] = survival.damage(
                            state,
                            unintended,
                            definition["damage"] + hits - 1,
                            bundle,
                            rng,
                            piercing=definition.get("piercing", False),
                            base_damage=definition["damage"],
                            barrier_armor=barrier,
                        )
                events.append(event)
    if net <= 0 and attack["skill"] == "ranged_combat":
        zone = state["scene"]["zones"][target["zone"]] if state["scene"] else {}
        armor = zone.get("outer_armor")
        if armor is not None and definition["damage"] >= max(
            0, armor - int(definition.get("piercing", False)) - int(attack["weak_spot"])
        ):
            die = rng.randint(1, 6)
            events.append({"type": "stray_hull_shot", "zone": target["zone"], "die": die})
            if die == 1:
                from lorekit.alien.environment import breach

                events.append(breach(state, [target["zone"]], zone.get("auto_seal", False)))
    if attack["weapon"] and not attack["special"]:
        instance, definition = survival.item(a, attack["weapon"], bundle)
        if definition.get("single_use"):
            instance["quantity"] -= 1
            if not instance["quantity"]:
                instance["equipped"] = False
        elif definition.get("single_shot"):
            instance["supply"] = 0
        elif definition.get("supply_type") and not (net > 0 and conserve):
            events.append(survival.supply(a, attack["weapon"], bundle, rng))
    # A target's pushed stun resistance can suspend the attack's burst offer.
    if (
        state["pending"]
        and state["pending"].get("type") == "check"
        and state["pending"].get("continuation", {}).get("kind") == "stun"
        and attack["full_auto"]
        and not attack.get("defer_burst")
        and net > 0
        and result["checks"][0]["pushes"] == 0
        and attack["burst"] < 3
    ):
        state["queue"].append({"type": "burst_offer", "attack": copy.deepcopy(attack)})
    # Full-auto followups are explicit decisions. No silent extra attacks.
    pushed = result["checks"][0]["pushes"] > 0
    if (
        state["pending"] is None
        and not attack.get("defer_burst")
        and attack["full_auto"]
        and net > 0
        and not pushed
        and attack["burst"] < 3
    ):
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
    events.extend(effects.drain(state, rng, bundle=bundle))
    return {"type": "attack_complete", "events": events, "pending": copy.deepcopy(state["pending"])}


def special_decide(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "special_choice" or p["attack"]["actor"] != intent.get("actor"):
        raise LoreKitError("No matching special attack decision")
    attack = p["attack"]
    a, target = actor(state, attack["actor"]), actor(state, attack["target"])
    if attack["special"] == "disarm":
        entry, item_definition = survival.item(target, intent.get("item"), bundle)
        if (
            entry.get("dropped")
            or not entry.get("equipped")
            or item_definition.get("armor") is not None
            or item_definition.get("vacuum_proof")
        ):
            raise LoreKitError("Disarm requires an item in the target's grasp")
        choice = intent.get("choice")
        if choice == "keep":
            new_id = intent.get("new_item_id", entry["id"])
            if not isinstance(new_id, str) or not new_id or any(i["id"] == new_id for i in a["inventory"]):
                raise LoreKitError("Choose a new_item_id that is unused in the recipient inventory")
            target["inventory"].remove(entry)
            entry["id"] = new_id
            a["inventory"].append(entry)
        elif choice == "throw":
            zone = intent.get("zone")
            if zone != a["zone"] and not scenes.traversable(scenes.border(state, a["zone"], zone)):
                raise LoreKitError("Throw into this zone or a reachable adjacent zone")
            entry.update(dropped=True, equipped=False, ground_zone=zone)
        else:
            raise LoreKitError("Choose keep or throw")
    else:
        a.setdefault("passed", []).append(attack["target"])
    state["pending"] = None
    return {
        "type": "special_resolved",
        "special": attack["special"],
        "actor": attack["actor"],
        "events": effects.drain(state, rng, bundle=bundle),
    }


def hit_decide(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "hit_reaction":
        raise LoreKitError("No pending hit reaction")
    result = copy.deepcopy(p["result"])
    choice = intent.get("choice")
    if choice == "bodyguard":
        bid = intent.get("actor")
        if bid not in p["candidates"]:
            raise LoreKitError("Actor cannot intercept this hit")
        scenes.spend(state, bid, "quick", out_of_turn=True)
        result["continuation"]["attack"]["target"] = bid
        result["continuation"]["attack"]["intercepted"] = True
    elif choice != "decline" or intent.get("actor") != p["attack"]["target"]:
        raise LoreKitError("Original target must decline, or an eligible Bodyguard can intercept")
    state["pending"] = None
    return finish(state, result, bundle, rng, reactions=False)


def finish_safety(state, result):
    c = result["continuation"]
    attack = c["attack"]
    attack.setdefault("redirected", []).append(attack["target"])
    if result["success"]:
        attack["target"] = c["target"]
    state["pending"] = {"type": "defense", "id": f"defense:{state['revision'] + 1}:redirect", "attack": attack}
    return {
        "type": "defense_required",
        "target": attack["target"],
        "decision_id": state["pending"]["id"],
        "checks": result["checks"],
    }


def finish_quick_shot(state, result, bundle, rng):
    attacks = result["continuation"]["attacks"]
    scores = result["successes"]
    simultaneous = scores[0] == scores[1]
    order = [0, 1] if scores[0] >= scores[1] else [1, 0]
    shots = []
    for index in order:
        attack = attacks[index]
        attack["defer_burst"] = True
        shots.append(
            {
                "type": "shot_followup",
                "actor": attack["actor"],
                "simultaneous": simultaneous,
                "result": {
                    "type": "check_complete",
                    "checks": [result["checks"][index]],
                    "successes": [scores[index]],
                    "success": scores[index] > 0,
                    "continuation": {"kind": "attack", "attack": attack},
                },
            }
        )
    for index, area_result in result["continuation"].get("area_results", {}).items():
        next(shot for shot in shots if shot["actor"] == attacks[int(index)]["actor"])["result"] = area_result
    if (
        "0" not in result["continuation"].get("area_results", {})
        and attacks[0]["full_auto"]
        and scores[0] > 0
        and result["checks"][0]["pushes"] == 0
        and attacks[0]["burst"] < 3
    ):
        shots.append({"type": "burst_offer", "attack": attacks[0]})
    state["queue"][0:0] = shots
    return {
        "type": "quick_shot_complete",
        "simultaneous": simultaneous,
        "order": [attacks[i]["actor"] for i in order],
        "events": effects.drain(state, rng, bundle=bundle),
        "pending": copy.deepcopy(state["pending"]),
    }
