"""Panic movement spends its budget and obeys zero-gravity physics."""

from __future__ import annotations

import copy

from lorekit.alien import checks, effects, scenes
from lorekit.alien.characters import incapacitated, talents
from lorekit.alien.state import actor
from lorekit.db import LoreKitError


def quick(state, aid):
    a = actor(state, aid)
    responses = a["panic_responses"]
    blocked = a.pop("interrupt_blocked", None)
    skipped = a.pop("skip_turn", None)
    a["panic_responses"] = [r for r in responses if r < 7]
    try:
        scenes.spend(state, aid, scenes.movement_cost(a), out_of_turn=True)
    finally:
        a["panic_responses"] = responses
        if blocked is not None:
            a["interrupt_blocked"] = blocked
        if skipped is not None:
            a["skip_turn"] = skipped


def run(state, plan, bundle, rng):
    aid = plan["actor"]
    a = actor(state, aid)
    while plan["index"] < len(plan["path"]):
        if incapacitated(a):
            return {"type": "panic_movement_aborted", "actor": aid}
        destination = plan["path"][plan["index"]]
        from lorekit.alien.environment import pressure_guard

        guarded = pressure_guard(
            state,
            {"op": "move", "actor": aid, "zone": destination},
            bundle,
            rng,
            after={"kind": "panic_motion", "plan": copy.deepcopy(plan)},
        )
        if guarded is not None:
            return guarded
        scenes.route(state, aid, destination)
        if plan["paid"]:
            quick(state, aid)
        zero_g = any(state["scene"]["zones"][zone].get("zero_g") for zone in (a["zone"], destination))
        if zero_g and not any(e["hook"] == "zero_g_immunity" for e in talents(a, bundle)):
            return checks.start(
                state,
                {
                    "actor": aid,
                    "skill": "mobility",
                    "_zero_g_move": True,
                    "continuation": {"kind": "forced_move", "plan": copy.deepcopy(plan), "zone": destination},
                },
                bundle,
                rng,
                queued=True,
            )
        scenes.move(state, aid, destination, bundle, forced=True, rng=rng)
        plan["index"] += 1
    return finish(state, plan, bundle, rng)


def resolve(state, result, bundle, rng):
    plan = result["continuation"]["plan"]
    if incapacitated(actor(state, plan["actor"])):
        return {"type": "panic_movement_aborted", "actor": plan["actor"]}
    if result["success"]:
        scenes.move(
            state, plan["actor"], result["continuation"]["zone"], bundle, forced=True, rng=rng, zero_g_checked=True
        )
        plan["index"] += 1
        return run(state, plan, bundle, rng)
    effects.stress(actor(state, plan["actor"]), 1)
    # A spin used this move. Later moves need a new route from the actual zone.
    if plan["paid"]:
        budget = state["combat"]["budgets"][plan["actor"]]
        if budget["quick"] < (1 if budget["full"] else 2):
            state["pending"] = {
                "type": "panic_choice",
                "actor": plan["actor"],
                "result": 10 if plan["kind"] == "flee" else 11,
                "subsequent_turn": True,
            }
    return {"type": "panic_zero_g_spin", "actor": plan["actor"], "checks": result["checks"]}


def finish(state, plan, bundle, rng):
    aid = plan["actor"]
    a = actor(state, aid)
    if incapacitated(a):
        return {"type": "panic_movement_aborted", "actor": aid}
    if plan["kind"] == "flee":
        if not plan["paid"]:
            a["stress"] = max(0, a["stress"] - 1)
            for friend in plan["friends"]:
                effects.stress(actor(state, friend), 1)
        return {
            "type": "panic_flee_moved",
            "actor": aid,
            "zone": a["zone"],
            "events": effects.drain(state, rng, bundle=bundle),
        }
    from lorekit.alien.environment import pressure_guard

    guarded = pressure_guard(
        state,
        {"op": "attack", "actor": aid, "target": plan["target"]},
        bundle,
        rng,
        after={"kind": "panic_motion", "plan": copy.deepcopy(plan)},
    )
    if guarded is not None:
        return guarded
    # A frenzy target remains locked. Approach is ordinary movement, not a
    # teleport. B22 defers an unreachable attack until a later turn.
    target = plan["target"]
    from lorekit.alien import combat, survival

    definition = survival.item(a, plan["weapon"], bundle)[1] if plan["weapon"] else {"maximum": 0}
    band = scenes.range_band(state, aid, target)
    if band > definition.get("maximum", 0) and band == 1 and plan.get("approach"):
        quick(state, aid)
        if state["scene"]["zones"][a["zone"]].get("zero_g") and not any(
            e["hook"] == "zero_g_immunity" for e in talents(a, bundle)
        ):
            return checks.start(
                state,
                {
                    "actor": aid,
                    "skill": "mobility",
                    "_zero_g_move": True,
                    "continuation": {"kind": "forced_move", "plan": copy.deepcopy(plan), "zone": None},
                },
                bundle,
                rng,
                queued=True,
            )
        scenes.approach(state, aid, target, bundle, paid=True)
        band = 0
    if band > definition.get("maximum", 0):
        return {"type": "frenzy_approaching", "actor": aid, "target": target}
    if plan["subsequent"]:
        responses = a["panic_responses"]
        a["panic_responses"] = [r for r in responses if r != 11]
        try:
            scenes.spend(state, aid, "full")
        except LoreKitError:
            return {"type": "frenzy_actions_exhausted", "actor": aid, "target": target}
        finally:
            a["panic_responses"] = responses
    return combat.declare(state, {"actor": aid, "target": target, "weapon": plan["weapon"]}, bundle, forced=True)
