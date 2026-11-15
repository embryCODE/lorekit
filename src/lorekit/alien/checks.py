"""Durable skill-check decisions, including ordered opposed pushes."""

from __future__ import annotations

from dataclasses import asdict

from lorekit.alien import effects
from lorekit.alien.characters import ATTRIBUTES, SKILLS, talents
from lorekit.alien.dice import PoolRoll, skill_roll
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError


def permission(a: dict, check: dict, bundle: dict) -> bool:
    extra = any(e["hook"] == "push_attribute" and e["attribute"] == check["attribute"] for e in talents(a, bundle))
    return (
        effects.eligible(a)
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
    modifier_from_substitution = 0
    for effect in talents(a, bundle):
        if effect["hook"] == "substitute_skill" and effect["skill"] == skill:
            skill = effect["to"]
            modifier_from_substitution += effect.get("value", 0)
    attribute = spec.get("attribute", SKILLS.get(skill, skill))
    if "attribute" not in spec:
        for effect in talents(a, bundle):
            if effect["hook"] == "attribute_override" and effect["skill"] == skill:
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
        if helper["dead"] or helper["health"] == 0 or 5 in helper["panic_responses"] or 5 in a["panic_responses"]:
            raise LoreKitError("Helper cannot currently provide help")
        if helper["zone"] != a["zone"] and not spec.get("remote_help", False):
            raise LoreKitError("Remote help requires an explicit GM declaration")
    modifier = spec.get("modifier", 0)
    if type(modifier) is not int:
        raise LoreKitError("modifier must be an integer")
    modifier += modifier_from_substitution
    for injury in a["injuries"]:
        for effect in injury["effects"]:
            if effect["hook"] == "injury_modifier" and skill in effect.get("skills", []):
                modifier += effect["value"]
    if effects.STRESS_ATTRIBUTE[attribute] in a["stress_responses"]:
        modifier -= 2
    for effect in talents(a, bundle):
        if (
            effect["hook"] == "skill_modifier"
            and effect["context"] == spec.get("context")
            and skill in effect.get("skills", [skill])
        ):
            modifier += effect["value"]
    # Action spending occurs only on the detached transaction copy.
    if state["mode"] == "combat":
        from lorekit.alien.scenes import spend

        for hid in helpers:
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
    return check


def start(state: dict, intent: dict, bundle: dict, rng, *, queued=False) -> dict:
    if state["pending"] is not None or (state["queue"] and not queued):
        raise LoreKitError("Resolve pending effects before starting a check")
    checks = [roll(state, intent, bundle, rng)]
    opponent = intent.get("opponent")
    if opponent is not None:
        if not isinstance(opponent, dict) or opponent.get("actor") == intent.get("actor"):
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
    state["pending"] = None
    return result


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
        p["index"] += 1
    elif choice == "push":
        if not permission(a, check, bundle):
            raise LoreKitError("This check cannot be pushed")
        cost = 2 if "jumpy" in a["stress_responses"] else 1
        effects.stress(a, cost)
        check["pushes"] += 1
        for key in ("base", "stress"):
            check["roll"][key] = [d if d == 6 else rng.randint(1, 6) for d in check["roll"][key]]
        check["roll"]["stress"].extend(rng.randint(1, 6) for _ in range(cost))
        if 1 in check["roll"]["stress"]:
            event = effects.stress_response(a, rng)
            check["events"].append(event)
            check["forced_failure"] |= event.get("forced_failure", False)
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
