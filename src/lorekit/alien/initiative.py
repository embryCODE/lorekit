"""Resumable initiative talent draws, retaining the unique ten-card deck."""

from __future__ import annotations

import copy

from lorekit.alien.characters import talents
from lorekit.alien.state import actor
from lorekit.db import LoreKitError


def has(state, unit, bundle, hook):
    return any(e["hook"] == hook for aid in unit for e in talents(actor(state, aid), bundle))


def start(state, expanded, bundle, rng, surprise):
    combat = state["combat"]
    deck = list(range(1, 11))
    rng.shuffle(deck)
    pending = {
        "type": "initiative",
        "deck": deck,
        "slots": [],
        "units": copy.deepcopy(expanded),
        "keep": [],
        "fast": [],
        "surprise": surprise or [],
        "stage": "keep",
    }
    # Retention reserves cards before new draws. Unit order is the GM's declared
    # participant/group order, with PCs before NPCs for Fast Reflexes.
    for unit in expanded:
        if has(state, unit, bundle, "combat_veteran") and combat["round"]:
            old = next((s["card"] for s in combat["slots"] if s["actors"] == unit), None)
            if old is not None:
                pending["keep"].append({"actors": unit, "card": old})
    state["pending"] = pending
    return advance(state, bundle, rng)


def advance(state, bundle, rng):
    p = state["pending"]
    if p["keep"]:
        p["choice"] = {"kind": "keep", **p["keep"][0]}
        return
    if p["stage"] == "keep":
        p["stage"] = "fast"
        # Surprise first-round cards and Hesitant have specific mandated draws.
        for unit in p["units"][:]:
            hesitant = any(6 in actor(state, aid)["panic_responses"] for aid in unit)
            ambush = any(aid in p["surprise"] for aid in unit)
            if hesitant or ambush:
                card = max(p["deck"]) if hesitant else min(p["deck"])
                p["deck"].remove(card)
                p["slots"].append({"actors": unit, "card": card})
                p["units"].remove(unit)
        p["fast"] = [u for u in p["units"] if has(state, u, bundle, "fast_reflexes")]
        p["fast"].sort(key=lambda u: all(actor(state, aid)["kind"] != "pc" for aid in u))
    if p["fast"]:
        if len(p["deck"]) < 2:
            raise LoreKitError("Not enough initiative cards for Fast Reflexes; group NPCs or record a ruling")
        p["choice"] = {"kind": "fast", "actors": p["fast"][0], "cards": [p["deck"].pop(), p["deck"].pop()]}
        return
    for unit in p["units"]:
        p["slots"].append({"actors": unit, "card": p["deck"].pop()})
    combat = state["combat"]
    combat["slots"] = sorted(p["slots"], key=lambda s: s["card"])
    combat["index"] = 0
    combat["round"] += 1
    combat["budgets"] = {aid: {"full": False, "quick": 0} for aid in combat["actors"]}
    for aid in combat["actors"]:
        actor(state, aid).pop("moved_this_round", None)
        actor(state, aid)["orders"] = []
    state["pending"] = None


def decide(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "initiative" or intent.get("actor") not in p["choice"]["actors"]:
        raise LoreKitError("No matching initiative decision")
    choice = p["choice"]
    if choice["kind"] == "keep":
        if type(intent.get("keep")) is not bool:
            raise LoreKitError("Choose keep:true or keep:false")
        if intent["keep"]:
            if choice["card"] not in p["deck"]:
                raise LoreKitError("Retained initiative card is unavailable")
            p["deck"].remove(choice["card"])
            p["slots"].append({"actors": choice["actors"], "card": choice["card"]})
            p["units"].remove(choice["actors"])
        p["keep"].pop(0)
    else:
        card = intent.get("card")
        if type(card) is not int or card not in choice["cards"]:
            raise LoreKitError("Choose one of the two drawn cards")
        p["slots"].append({"actors": choice["actors"], "card": card})
        p["deck"].extend(c for c in choice["cards"] if c != card)
        rng.shuffle(p["deck"])
        p["fast"].pop(0)
        p["units"].remove(choice["actors"])
    advance(state, bundle, rng)
    return {
        "type": "initiative_decision",
        "combat": copy.deepcopy(state["combat"]),
        "pending": copy.deepcopy(state["pending"]),
    }
