"""Alien stress responses and ordered panic consequences (Beta 2 pp. 44, 72–74)."""

from __future__ import annotations

from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError

STRESS_NAMES = {1: "jumpy", 2: "tunnel_vision", 3: "aggravated", 4: "shakes", 5: "frantic", 6: "deflated"}
STRESS_ATTRIBUTE = {"wits": "tunnel_vision", "empathy": "aggravated", "agility": "shakes", "strength": "frantic"}


def eligible(a: dict) -> bool:
    return a["species"] == "human" and a["kind"] == "pc" and a["health"] > 0 and not a["dead"]


def stress(a: dict, amount: int) -> int:
    integer(amount, "stress increase")
    if eligible(a):
        a["stress"] += amount
    return a["stress"]


def stress_response(a: dict, rng) -> dict:
    if not eligible(a):
        return {"type": "stress_response", "immune": True}
    die = rng.randint(1, 6)
    result = die + a["stress"] - a["resolve"]
    if result >= 7:
        stress(a, 1)
        return {"type": "stress_response", "die": die, "result": result, "forced_failure": True}
    name = STRESS_NAMES.get(result)
    if name:
        current = a["stress_responses"]
        if name == "jumpy" and "deflated" in current:
            pass
        elif name in current:
            stress(a, 1)
        else:
            current.append(name)
            if name == "deflated" and "jumpy" in current:
                current.remove("jumpy")
    return {"type": "stress_response", "die": die, "result": result, "response": name}


def friendly(state: dict, source_id: str) -> list[str]:
    source = actor(state, source_id)
    return [
        aid
        for aid, a in sorted(state["actors"].items())
        if aid != source_id
        and a["zone"] is not None
        and a["zone"] == source["zone"]
        and a.get("side", a["kind"]) == source.get("side", source["kind"])
        and eligible(a)
    ]


def enqueue_panic(state: dict, actor_id: str, reason: str) -> None:
    a = actor(state, actor_id)
    if not isinstance(reason, str) or not reason.strip():
        raise LoreKitError("A concrete panic trigger is required")
    if eligible(a):
        state["queue"].append({"type": "panic", "actor": actor_id, "reason": reason})


def panic(state: dict, actor_id: str, rng) -> dict:
    a = actor(state, actor_id)
    if not eligible(a):
        return {"type": "panic", "actor": actor_id, "immune": True}
    die = rng.randint(1, 6)
    value = min(12, max(0, die + a["stress"] - a["resolve"]))
    current = a["panic_responses"]
    while value in current and value < 12:
        value += 1
    event = {"type": "panic", "actor": actor_id, "die": die, "result": value}
    a["highest_panic"] = max(value, a.get("highest_panic", 0))
    if value == 0:
        return event
    if value == 1:
        stress(a, 1)
    elif value in (2, 3, 4):
        # Alert radius and resource/item selection are explicit GM choices.
        state["pending"] = {"type": "panic_choice", "actor": actor_id, "result": value}
    else:
        if value not in current:
            current.append(value)
        if value >= 7:
            a["interrupt_blocked"] = True
        if value in (7, 8, 9):
            a["skip_turn"] = True
        if value == 8:
            zone = (state.get("scene") or {}).get("zones", {}).get(a["zone"], {})
            if zone.get("cluttered"):
                a["cover"] = "full"
                a["stress"] = max(0, a["stress"] - 1)
            else:
                a["panic_responses"].remove(8)
                a["panic_responses"].append(9) if 9 not in a["panic_responses"] else None
                value = 9
                event["result"] = 9
        if value == 9:
            a["stress"] = max(0, a["stress"] - 1)
            for aid in friendly(state, actor_id):
                enqueue_panic(state, aid, "witnessed_scream")
        if value in (10, 11):
            state["pending"] = {"type": "panic_choice", "actor": actor_id, "result": value}
            if value == 11:
                for aid in friendly(state, actor_id):
                    enqueue_panic(state, aid, "witnessed_frenzy")
    return event


def drain(state: dict, rng, limit: int = 100, bundle=None) -> list[dict]:
    events = []
    while state["queue"] and state["pending"] is None and len(events) < limit:
        entry = state["queue"].pop(0)
        if entry["type"] == "panic":
            events.append(panic(state, entry["actor"], rng))
        elif entry["type"] == "due":
            from lorekit.alien import content, survival

            result = survival.due(state, entry, bundle or content.read(state["content_hash"]), rng)
            if result.get("type") == "check_complete":
                result = survival.finish_due(state, result, bundle or content.read(state["content_hash"]), rng)
            events.append(result)
        else:
            state["queue"].insert(0, entry)
            raise LoreKitError("Unsupported queued effect")
    if len(events) >= limit and state["queue"]:
        events.append({"type": "queue_suspended", "reason": "safety_limit", "remaining": len(state["queue"])})
    return events


def stop_panic(a: dict) -> None:
    a["panic_responses"] = []
    a.pop("interrupt_blocked", None)
    a.pop("skip_turn", None)
