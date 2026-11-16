"""Alien stress responses and ordered panic consequences (Beta 2 pp. 44, 72–74)."""

from __future__ import annotations

import copy

from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError

STRESS_NAMES = {1: "jumpy", 2: "tunnel_vision", 3: "aggravated", 4: "shakes", 5: "frantic", 6: "deflated"}
STRESS_ATTRIBUTE = {"wits": "tunnel_vision", "empathy": "aggravated", "agility": "shakes", "strength": "frantic"}


def eligible(a: dict) -> bool:
    return a["species"] == "human" and a["kind"] == "pc" and a["health"] > 0 and not a["dead"]


def stress(a: dict, amount: int) -> int:
    integer(amount, "stress increase")
    if eligible(a) and amount:
        amount += int(a.get("trauma_traits", {}).get("extra_stress", False))
        a["stress"] += amount
        if a.get("alternate_personality"):
            alternate = a["alternate_personality"]
            a["alternate_personality"] = {key: a[key] for key in alternate}
            a.update(alternate)
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


def panic(state: dict, actor_id: str, rng, *, bundle=None, rolled=None) -> dict:
    a = actor(state, actor_id)
    if not eligible(a):
        return {"type": "panic", "actor": actor_id, "immune": True}
    die = rng.randint(1, 6) if rolled is None else rolled
    value = min(12, max(0, die + a["stress"] - a["resolve"]))
    if value >= 8 and a.get("trauma_traits", {}).get("violent"):
        value = 11
    current = a["panic_responses"]
    while value in current and value < 12:
        value += 1
    event = {"type": "panic", "actor": actor_id, "die": die, "result": value}
    a["highest_panic"] = max(value, a.get("highest_panic", 0))
    if value >= 9 and bundle is not None and rolled is None:
        from lorekit.alien.characters import talents

        if any(e["hook"] == "overkill" for e in talents(a, bundle)):
            state["pending"] = {"type": "overkill_choice", "actor": actor_id, "die": die, "result": value}
            return {**event, "pending": copy.deepcopy(state["pending"])}
    if value == 0:
        return event
    if value == 1:
        stress(a, 1)
    elif value in (2, 3, 4):
        # Alert radius and resource/item selection are explicit GM choices.
        state["pending"] = {"type": "panic_choice", "actor": actor_id, "result": value}
    else:
        if value >= 10:
            stronger = [r for r in current if r >= 10]
            if stronger and max(stronger) > value:
                # The stronger incompatible enduring response takes precedence.
                return {**event, "suppressed_by": max(stronger)}
            current[:] = [r for r in current if r < 10]
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
            from lorekit.alien import content

            events.append(panic(state, entry["actor"], rng, bundle=bundle or content.read(state["content_hash"])))
        elif entry["type"] == "shot_followup":
            from lorekit.alien import combat, content

            a = actor(state, entry["actor"])
            if not entry["simultaneous"] and (a["health"] == 0 or a["dead"]):
                events.append({"type": "quick_shot_aborted", "actor": entry["actor"]})
            else:
                from lorekit.alien import area

                resolver = area.finish if entry["result"]["continuation"]["kind"] == "area" else combat.finish
                events.append(resolver(state, entry["result"], bundle or content.read(state["content_hash"]), rng))
        elif entry["type"] == "burst_offer":
            attack = entry["attack"]
            a = actor(state, attack["actor"])
            if not a["dead"] and a["health"] > 0:
                attack.pop("defer_burst", None)
                state["pending"] = {"type": "burst", "attack": attack}
                events.append({"type": "burst_offer", "actor": attack["actor"]})
        elif entry["type"] == "lucky_injury":
            if actor(state, entry["actor"])["dead"]:
                continue
            state["pending"] = entry
            events.append({"type": "lucky_injury", "pending": copy.deepcopy(entry)})
        elif entry["type"] == "egg_disturbance":
            from lorekit.alien import checks, content
            from lorekit.alien.engine import complete_check

            egg = actor(state, entry["egg"])
            if egg["dead"] or egg.get("opened") or egg.get("dormant"):
                continue
            local = bundle or content.read(state["content_hash"])
            result = checks.start(
                state,
                {
                    "actor": entry["actor"],
                    "skill": "mobility",
                    "continuation": {"kind": "egg_disturbance", "actor": entry["actor"], "egg": entry["egg"]},
                },
                local,
                rng,
                queued=True,
            )
            events.append(complete_check(state, result, local, rng))
        elif entry["type"] == "injury_infection":
            from lorekit.alien import checks, content, survival

            local = bundle or content.read(state["content_hash"])
            result = checks.start(
                state,
                {
                    "actor": entry["actor"],
                    "skill": "stamina",
                    "passive": True,
                    "no_push": True,
                    "modifier": -entry["penalty"],
                    "continuation": {
                        "kind": "disease_exposure",
                        "actor": entry["actor"],
                        "unit": "shift",
                        "penalty": entry["penalty"],
                    },
                },
                local,
                rng,
                queued=True,
            )
            if result.get("type") == "check_complete":
                result = survival.finish_exposure(state, result)
            events.append(result)
        elif entry["type"] == "direct_trauma":
            from lorekit.alien import content, trauma

            local = bundle or content.read(state["content_hash"])
            dice = [rng.randint(1, 6), rng.randint(1, 6)]
            key = str(dice[0] * 10 + dice[1])
            definition = local["collections"].get("traumas", {}).get(key)
            if definition is None:
                raise LoreKitError("Missing direct trauma result")
            trauma.apply(state, entry["actor"], {**definition, "id": key})
            events.append({"type": "direct_trauma", "actor": entry["actor"], "dice": dice, "result": key})
        elif entry["type"] == "infection":
            from lorekit.alien import content, creatures

            events.append(creatures.infection_event(state, entry, bundle or content.read(state["content_hash"]), rng))
        elif entry["type"] == "mature":
            from lorekit.alien import content, creatures

            events.append(creatures.mature(state, entry["actor"], bundle or content.read(state["content_hash"])))
        elif entry["type"] == "hatch_attack":
            from lorekit.alien import content, creatures

            events.append(
                creatures.act(
                    state,
                    {"actor": entry["actor"], "target": entry["target"], "feasible": [1, 2, 3, 4, 5, 6]},
                    bundle or content.read(state["content_hash"]),
                    rng,
                    immediate=True,
                )
            )
        elif entry["type"] == "radiation":
            from lorekit.alien import content, environment

            a = actor(state, entry["actor"])
            if a.get("hotspot"):
                events.append(
                    environment.radiation(
                        state,
                        {"actor": entry["actor"], "level": a["radiation_level"]},
                        bundle or content.read(state["content_hash"]),
                        rng,
                    )
                )
        elif entry["type"] == "panic_turn":
            a = actor(state, entry["actor"])
            if eligible(a) and entry["result"] in a["panic_responses"]:
                state["pending"] = {
                    "type": "panic_choice",
                    "actor": entry["actor"],
                    "result": entry["result"],
                    "subsequent_turn": True,
                }
        elif entry["type"] == "due":
            from lorekit.alien import content, survival

            result = survival.due(state, entry, bundle or content.read(state["content_hash"]), rng)
            if result.get("type") == "check_complete" and result.get("continuation", {}).get("kind") == "stun":
                result = survival.finish_stun(state, result)
            elif result.get("type") == "check_complete":
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
    a.pop("frenzy_target", None)
    a.pop("safe_flee_zone", None)
