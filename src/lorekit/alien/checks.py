"""Durable skill-check decisions, including ordered opposed pushes."""

from __future__ import annotations

from dataclasses import asdict

from lorekit.alien import effects
from lorekit.alien.characters import ATTRIBUTES, SKILLS, incapacitated, talents
from lorekit.alien.dice import PoolRoll, skill_roll
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def permission(a: dict, check: dict, bundle: dict) -> bool:
    extra = any(e["hook"] == "push_attribute" and e["attribute"] == check["attribute"] for e in talents(a, bundle))
    return (
        not a.get("trauma_traits", {}).get("no_push")
        and effects.eligible(a)
        and not check["passive"]
        and not check.get("no_push", False)
        and not any(d == 1 for d in check["roll"]["stress"])
        and "deflated" not in a["stress_responses"]
        and check["pushes"] < (2 if extra else 1)
    )


def roll(state: dict, spec: dict, bundle: dict, rng) -> dict:
    a = actor(state, spec.get("actor"))
    if a["dead"]:
        raise LoreKitError("Dead actor cannot make checks")
    if a["species"] == "human" and a["health"] == 0 and not spec.get("no_push"):
        raise LoreKitError("Broken human cannot make ordinary checks")
    skill = spec.get("skill")
    if skill not in SKILLS and skill not in ATTRIBUTES:
        raise LoreKitError("Unknown skill or attribute")
    if a["species"] == "xenomorph" and "_base_dice" not in spec and skill not in a["skills"]:
        raise LoreKitError("Creature has no supported rating for this skill; use its signature actions")
    if (
        a.get("trauma_traits", {}).get("cannot_speak")
        and skill in ("command", "manipulation")
        and spec.get("nonverbal") is not True
    ):
        raise LoreKitError("Speech loss requires an explicitly nonverbal social action")
    if spec.get("_attribute_only"):
        pool = skill_roll(rng, attribute=a["attributes"][skill], skill=0, stress=0)
        return {
            "actor": spec["actor"],
            "attribute": skill,
            "skill": skill,
            "passive": False,
            "no_push": True,
            "attribute_only": True,
            "roll": {key: list(value) for key, value in asdict(pool).items()},
            "pushes": 0,
            "forced_failure": False,
            "events": [],
            "story_dice": [],
        }
    modifier_from_substitution = 0
    for effect in talents(a, bundle):
        if (
            effect["hook"] == "substitute_skill"
            and effect["skill"] == skill
            and (effect.get("context") is None or effect["context"] == spec.get("context"))
            and spec.get("talent_substitution", True)
        ):
            skill = effect["to"]
            modifier_from_substitution += effect.get("value", 0)
    attribute = spec.get("attribute", SKILLS.get(skill, skill))
    if "attribute" not in spec:
        for effect in sorted(talents(a, bundle), key=lambda e: e.get("context") is not None):
            if (
                effect["hook"] == "attribute_override"
                and effect["skill"] == skill
                and (effect.get("context") is None or effect["context"] == spec.get("context"))
            ):
                attribute = effect["attribute"]
    if attribute not in ATTRIBUTES:
        raise LoreKitError("Unknown attribute substitution")
    passive = spec.get("passive", False)
    if type(passive) is not bool:
        raise LoreKitError("passive must be boolean")
    helpers = spec.get("helpers", [])
    if not isinstance(helpers, list) or any(not isinstance(h, str) for h in helpers):
        raise LoreKitError("helpers must be actor IDs")
    if len(helpers) > 3 or len(set(helpers)) != len(helpers) or spec["actor"] in helpers:
        raise LoreKitError("Up to three distinct other helpers are allowed")
    for hid in helpers:
        helper = actor(state, hid)
        if incapacitated(helper) or 5 in helper["panic_responses"] or 5 in a["panic_responses"]:
            raise LoreKitError("Helper cannot currently provide help")
        if helper["zone"] != a["zone"] and not spec.get("remote_help", False):
            raise LoreKitError("Remote help requires an explicit GM declaration")
    modifier = spec.get("modifier", 0)
    if type(modifier) is not int:
        raise LoreKitError("modifier must be an integer")
    gear = spec.get("gear", [])
    if not isinstance(gear, list) or any(not isinstance(i, str) for i in gear) or len(set(gear)) != len(gear):
        raise LoreKitError("Gear must contain distinct inventory instance IDs")
    from lorekit.alien.survival import item

    used_gear = []
    gear_two_hands = False
    for instance_id in gear:
        instance, definition = item(a, instance_id, bundle)
        gear_two_hands |= definition.get("hands", 1) > 1
        if 3 in a.get("system_damage", []) and definition.get("hands", 1) > 1:
            raise LoreKitError("Damaged arm servos allow only one-handed gear")
        if instance.get("dropped") or instance.get("quantity", 1) <= 0:
            raise LoreKitError("Gear is not available")
        modifiers = [
            e["value"]
            for e in definition.get("check_modifiers", [])
            if e["skill"] == skill and e["context"] == spec.get("context")
        ]
        if not modifiers:
            raise LoreKitError("Gear has no supported modifier for this check context")
        modifier += sum(modifiers)
        if definition.get("single_use"):
            used_gear.append(instance)
    if a.get("dose_penalty_shift") == state["clocks"]["shift"]:
        modifier -= a.get("dose_penalty", 0)
    modifier += modifier_from_substitution
    if state["scene"] and a["zone"] is not None and state["scene"]["zones"][a["zone"]].get("zero_g"):
        immune = any(e["hook"] == "zero_g_immunity" for e in talents(a, bundle))
        if (
            not immune
            and not spec.get("_zero_g_move")
            and skill in ("close_combat", "heavy_machinery", "mobility", "ranged_combat")
        ):
            modifier -= 2
    modifier -= a.get("trauma_penalties", {}).get(attribute, 0)
    if state["combat"] and spec.get("order"):
        matched = next(
            (
                order
                for order in a.get("orders", [])
                if order["order"] == spec["order"] and order["round"] == state["combat"]["round"]
            ),
            None,
        )
        if matched:
            modifier += matched["bonus"]
            a["orders"].remove(matched)
    for injury in a["injuries"]:
        for effect in injury["effects"]:
            if effect["hook"] == "injury_modifier" and skill in effect.get("skills", []):
                if effect.get("condition") and not (spec.get(effect["condition"], False) or gear_two_hands):
                    continue
                modifier += effect["value"]
    if effects.STRESS_ATTRIBUTE[attribute] in a["stress_responses"]:
        modifier -= 2
    for effect in talents(a, bundle):
        if (
            effect["hook"] == "skill_modifier"
            and effect["context"] == spec.get("context")
            and skill in effect.get("skills", [skill])
            and (effect.get("attribute") is None or effect["attribute"] == attribute)
        ):
            modifier += effect["value"]
    # Action spending occurs only on the detached transaction copy.
    if state["mode"] == "combat":
        from lorekit.alien.scenes import spend

        for hid in helpers:
            helper = actor(state, hid)
            offer = helper.get("help_offer")
            if (
                offer
                and offer["actor"] == spec["actor"]
                and offer["skill"] in (skill, spec.get("skill"))
                and offer["round"] == state["combat"]["round"]
            ):
                helper.pop("help_offer")
            else:
                spend(state, hid, "full", out_of_turn=True)
    omit = integer(spec.get("omit_stress", 0), "omit_stress", 0, 2)
    allowed_omit = max([0] + [e["value"] for e in talents(a, bundle) if e["hook"] == "omit_stress"])
    if omit > allowed_omit:
        raise LoreKitError("Omitting stress dice requires Steady Hands")
    if spec.get("reckless", False):
        if not any(e["hook"] == "stress_before_roll" for e in talents(a, bundle)):
            raise LoreKitError("Pre-roll stress increase requires the matching talent")
        effects.stress(a, 1)
    use_stress = a["stress"] if a["kind"] == "pc" and a["species"] == "human" else 0
    if spec.get("no_stress", False):
        use_stress = 0
    pool = skill_roll(
        rng,
        attribute=0 if "_base_dice" in spec else a["attributes"][attribute],
        skill=spec.get("_base_dice", a["skills"].get(skill, 0)),
        modifier=modifier,
        helpers=len(helpers),
        stress=max(0, use_stress - omit),
    )
    for instance in used_gear:
        instance["quantity"] -= 1
        if not instance["quantity"]:
            instance["equipped"] = False
    check = {
        "actor": spec["actor"],
        "attribute": attribute,
        "skill": skill,
        "passive": passive,
        "no_push": spec.get("no_push", False),
        "roll": {k: list(v) for k, v in asdict(pool).items()},
        "pushes": 0,
        "story_dice": [],
        "events": [],
        "forced_failure": False,
    }
    if pool.stress_symbols and not passive:
        event = effects.stress_response(a, rng)
        check["events"].append(event)
        check["forced_failure"] = event.get("forced_failure", False)
    if skill == "mobility":
        from lorekit.alien.survival import supply, sync_air

        for instance in a["inventory"]:
            if instance.get("equipped") and instance.get("active_air") and not instance.get("dropped"):
                check["events"].append(supply(a, instance["id"], bundle, rng))
        sync_air(state, spec["actor"])
    check["events"].extend(bleeding(state, spec["actor"], skill, bundle, rng))
    return check


def bleeding(state, aid, skill, bundle, rng):
    from lorekit.alien.survival import damage

    a = actor(state, aid)
    events = []
    for injury in list(a["injuries"]):
        for effect in injury["effects"]:
            if effect["hook"] == "bleeding_on_skill" and skill in effect.get("skills", []):
                events.extend(damage(state, aid, effect["value"], bundle, rng, bypass=True))
    return events


def start(state: dict, intent: dict, bundle: dict, rng, *, queued=False) -> dict:
    if state["pending"] is not None or (state["queue"] and not queued):
        raise LoreKitError("Resolve pending effects before starting a check")
    checks = [roll(state, intent, bundle, rng)]
    opponents = ([intent["opponent"]] if intent.get("opponent") is not None else []) + intent.get("opponents", [])
    for opponent in opponents:
        if not isinstance(opponent, dict) or (opponent.get("actor") == intent.get("actor") and not intent.get("_area")):
            raise LoreKitError("Opposed check needs a distinct actor specification")
        checks.append(roll(state, opponent, bundle, rng))
    pending = {
        "type": "check",
        "id": f"check:{state['revision'] + 1}",
        "checks": checks,
        "index": 0,
        "open": intent.get("open", False),
        "continuation": intent.get("continuation"),
    }
    state["pending"] = pending
    return advance(state, bundle)


def successes(check: dict) -> int:
    if check["forced_failure"]:
        return 0
    return sum(d == 6 for dice in check["roll"].values() for d in dice)


def advance(state: dict, bundle: dict) -> dict:
    p = state["pending"]
    while p["index"] < len(p["checks"]):
        check = p["checks"][p["index"]]
        a = actor(state, check["actor"])
        can_story = (
            not check["forced_failure"]
            and not check.get("attribute_only")
            and a["kind"] == "pc"
            and a["story_points"] > 0
            and any(d != 6 for d in check["roll"]["base"])
        )
        if permission(a, check, bundle) or can_story:
            return {"pending": p, "events": check["events"]}
        p["index"] += 1
    values = [successes(c) for c in p["checks"]]
    result = {
        "type": "check_complete",
        "checks": p["checks"],
        "successes": values,
        "success": values[0] > (values[1] if len(values) == 2 else 0),
        "tie": len(values) == 2 and values[0] == values[1],
        "open": p["open"],
        "continuation": p["continuation"],
    }
    if p["continuation"] is None:
        budgets = {}
        for index, check in enumerate(p["checks"]):
            net = values[index] - (values[1 - index] if len(values) == 2 else 0)
            available = max(0, net - 1)
            actor(state, check["actor"])["narrative_successes"] = {
                "check_id": p["id"],
                "remaining": available,
                "benefits": [],
            }
            budgets[check["actor"]] = available
        result["benefit_budget"] = budgets
        result["check_id"] = p["id"]
    state["pending"] = None
    return result


def benefits(state, intent):
    a = actor(state, intent.get("actor"))
    balance = a.get("narrative_successes")
    claims = intent.get("benefits")
    if not balance or balance["check_id"] != intent.get("check_id"):
        raise LoreKitError("No matching completed generic check")
    if (
        not isinstance(claims, list)
        or not claims
        or any(not isinstance(v, str) or not v.strip() for v in claims)
        or len(set(claims)) != len(claims)
        or len(claims) > balance["remaining"]
        or set(claims) & set(balance["benefits"])
    ):
        raise LoreKitError("Record distinct GM benefits within the remaining extra-success budget")
    balance["remaining"] -= len(claims)
    balance["benefits"].extend(claims)
    return {"type": "narrative_benefits", "actor": intent["actor"], **balance}


def decide(state: dict, intent: dict, bundle: dict, rng) -> dict:
    p = state["pending"]
    if not p or p["type"] != "check" or p["id"] != intent.get("decision_id"):
        raise LoreKitError("Pending check ID does not match")
    check = p["checks"][p["index"]]
    if intent.get("actor") != check["actor"]:
        raise LoreKitError("This actor's decision is not due yet")
    a = actor(state, check["actor"])
    choice = intent.get("choice")
    if choice == "accept":
        if a.get("trauma_traits", {}).get("must_push") and permission(a, check, bundle):
            raise LoreKitError("Obsessive trauma requires pushing this eligible roll")
        p["index"] += 1
    elif choice == "push":
        if not permission(a, check, bundle):
            raise LoreKitError("This check cannot be pushed")
        cost = 2 if "jumpy" in a["stress_responses"] else 1
        before_stress = a["stress"]
        effects.stress(a, cost)
        cost = a["stress"] - before_stress
        check["pushes"] += 1
        for key in ("base", "stress"):
            check["roll"][key] = [d if d == 6 else rng.randint(1, 6) for d in check["roll"][key]]
        check["roll"]["stress"].extend(rng.randint(1, 6) for _ in range(cost))
        if 1 in check["roll"]["stress"]:
            event = effects.stress_response(a, rng)
            check["events"].append(event)
            check["forced_failure"] |= event.get("forced_failure", False)
        check["events"].extend(bleeding(state, check["actor"], check["skill"], bundle, rng))
    elif choice == "story_point":
        index = integer(intent.get("die"), "base die index", 0)
        if a["kind"] != "pc" or not a["story_points"] or index >= len(check["roll"]["base"]):
            raise LoreKitError("No eligible story-point die")
        if check["roll"]["base"][index] == 6 or index in check["story_dice"]:
            raise LoreKitError("Cannot spend a story point on this die")
        a["story_points"] -= 1
        check["roll"]["base"][index] = 6
        check["story_dice"].append(index)
    else:
        raise LoreKitError("choice must be accept, push or story_point")
    return advance(state, bundle)
