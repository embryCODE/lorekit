"""Opt-in trauma checks and mechanical traits supplied by a local D66 table."""

from __future__ import annotations

import copy

from lorekit.alien import checks
from lorekit.alien.state import actor
from lorekit.db import LoreKitError

TRAITS = {
    "no_push",
    "must_push",
    "rest_alone",
    "rest_alcohol",
    "no_rally",
    "minor_stress",
    "phobia",
    "cannot_speak",
    "nightmares",
    "extra_stress",
    "violent",
    "hallucinations",
    "amnesia",
    "personality_split",
}
HOOKS = {"trauma_trait", "attribute_penalty", "resolve_delta", "trauma_threshold", "agenda_change"}


def start(state, intent, bundle, rng):
    if not state["options"]["mental_trauma"]:
        raise LoreKitError("Mental trauma is disabled for this session")
    aid = intent.get("actor")
    a = actor(state, aid)
    if a["species"] != "human" or a["kind"] != "pc":
        return {"type": "trauma_immune", "actor": aid}
    chapter = intent.get("chapter")
    if not isinstance(chapter, str) or not chapter.strip():
        raise LoreKitError("Name this completed play session for the trauma check")
    if chapter in a.get("trauma_checked", []):
        raise LoreKitError("This play session was already checked for trauma")
    a.setdefault("trauma_checked", []).append(chapter)
    if a.get("highest_panic", 0) < a.get("trauma_threshold", 9):
        a["highest_panic"] = 0
        return {"type": "trauma_not_triggered", "actor": aid}
    a["highest_panic"] = 0
    return checks.start(
        state,
        {
            "actor": aid,
            "skill": "empathy",
            "no_push": True,
            "no_stress": True,
            "continuation": {"kind": "trauma", "actor": aid},
        },
        bundle,
        rng,
    )


def apply(state, aid, definition):
    a = actor(state, aid)
    for effect in definition["effects"]:
        hook = effect["hook"]
        if hook == "trauma_trait":
            a.setdefault("trauma_traits", {})[effect["trait"]] = True
        elif hook == "attribute_penalty":
            a.setdefault("trauma_penalties", {}).setdefault(effect["attribute"], 0)
            a["trauma_penalties"][effect["attribute"]] += effect["value"]
        elif hook == "resolve_delta":
            a["resolve"] += effect["value"]
        elif hook == "trauma_threshold":
            a["trauma_threshold"] = effect["value"]
        elif hook == "agenda_change":
            state["pending"] = {"type": "trauma_choice", "actor": aid, "choice": "agenda"}
    if a.get("trauma_traits", {}).get("personality_split") and not a.get("alternate_personality"):
        state["pending"] = {"type": "trauma_choice", "actor": aid, "choice": "personality"}
    a.setdefault("traumas", []).append(definition["id"])


def finish(state, result, bundle, rng):
    aid = result["continuation"]["actor"]
    if result["success"]:
        return {"type": "trauma_avoided", "actor": aid, "checks": result["checks"]}
    entries = bundle["collections"].get("traumas", {})
    if set(entries) != {str(t * 10 + u) for t in range(1, 7) for u in range(1, 7)}:
        raise LoreKitError("Trauma requires a complete local D66 table")
    dice = [rng.randint(1, 6), rng.randint(1, 6)]
    key = str(dice[0] * 10 + dice[1])
    apply(state, aid, {**entries[key], "id": key})
    return {"type": "trauma", "actor": aid, "dice": dice, "result": key, "checks": result["checks"]}


def decide(state, intent, bundle, rng):
    from lorekit.alien.characters import build

    p = state["pending"]
    if not p or p["type"] != "trauma_choice" or p["actor"] != intent.get("actor"):
        raise LoreKitError("No matching trauma choice")
    a = actor(state, p["actor"])
    if p["choice"] == "agenda":
        agenda = intent.get("agenda")
        if not isinstance(agenda, str) or not agenda.strip():
            raise LoreKitError("An explicit replacement agenda is required")
        a["private"]["agenda"] = agenda
    else:
        spec = intent.get("personality")
        if not isinstance(spec, dict):
            raise LoreKitError("Provide an imported alternate personality build")
        candidates = sorted(bundle["collections"].get("talents", {}))
        if not candidates:
            raise LoreKitError("Alternate personality needs a local talent pool")
        spec = copy.deepcopy(spec)
        spec["talents"] = [rng.choice(candidates)]
        alternate = build(spec, bundle, imported=True)
        for key in ("strength", "agility"):
            if alternate["attributes"][key] != a["attributes"][key]:
                raise LoreKitError("Alternate personality retains physical attributes")
        keys = ("name", "attributes", "skills", "talents", "private", "resolve")
        a["alternate_personality"] = {key: copy.deepcopy(alternate[key]) for key in keys}
    state["pending"] = None
    return {"type": "trauma_choice_resolved", "actor": p["actor"]}


def event(state, intent):
    from lorekit.alien import effects

    aid = intent.get("actor")
    a = actor(state, aid)
    kind = intent.get("event")
    trait = {
        "minor_concern": "minor_stress",
        "hallucination": "hallucinations",
        "phobia": "phobia",
        "sleep": "nightmares",
    }.get(kind)
    if trait is None or not a.get("trauma_traits", {}).get(trait):
        raise LoreKitError("Actor does not have this trauma trigger")
    if not isinstance(intent.get("reason"), str) or not intent["reason"].strip():
        raise LoreKitError("A GM description of the trigger is required")
    if kind in ("hallucination", "sleep"):
        key = f"trauma_{kind}_shift"
        if a.get(key) == state["clocks"]["shift"]:
            raise LoreKitError("This trauma trigger already occurred this shift")
        a[key] = state["clocks"]["shift"]
    if kind == "phobia":
        effects.enqueue_panic(state, aid, intent["reason"])
    else:
        effects.stress(a, 1)
    return {"type": "trauma_trigger", "actor": aid, "event": kind, "reason": intent["reason"]}
