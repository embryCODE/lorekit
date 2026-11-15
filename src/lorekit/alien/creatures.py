"""Data-driven creature actors and signature selection, using local content."""

from __future__ import annotations

import copy

from lorekit.alien import effects, scenes
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError

ATTACK_HOOKS = {"attack", "panic", "stress", "drop_item", "move", "grapple", "facehug", "instant_kill"}


def build(cid, name, bundle):
    if not isinstance(name, str) or not name.strip():
        raise LoreKitError("Creature name is required")
    definition = bundle["collections"].get("creatures", {}).get(cid)
    if definition is None:
        raise LoreKitError("Unknown creature content ID")
    return {
        "name": name,
        "species": "xenomorph",
        "kind": "npc",
        "content_id": cid,
        "health": definition["health"],
        "health_max": definition["health"],
        "armor": definition.get("armor", 0),
        "speed": definition["speed"],
        "attributes": {"strength": 0, "agility": 0, "wits": 0, "empathy": 0},
        "skills": copy.deepcopy(definition["skills"]),
        "talents": [],
        "inventory": [],
        "injuries": [],
        "stress": 0,
        "resolve": 0,
        "story_points": 0,
        "effects": [],
        "stress_responses": [],
        "panic_responses": [],
        "private": {},
        "revealed": False,
        "secret_identity": False,
        "zone": None,
        "hidden": False,
        "dead": False,
        "fatigued": False,
        "rads": 0,
    }


def select(state, aid, feasible, bundle, rng):
    a = actor(state, aid)
    definition = bundle["collections"].get("creatures", {}).get(a.get("content_id"))
    if definition is None:
        raise LoreKitError("Actor is not a supported creature")
    if not isinstance(feasible, list) or any(type(v) is not int or not 1 <= v <= 6 for v in feasible):
        raise LoreKitError("GM must declare feasible signature die results")
    outcomes = [i for i in range(1, 7) if i in feasible and definition["attacks"][str(i)]["id"] != a.get("last_attack")]
    if not outcomes:
        raise LoreKitError("No feasible signature: choose movement or an explicit GM ruling")
    value = rng.choice(outcomes)
    selected = copy.deepcopy(definition["attacks"][str(value)])
    a["last_attack"] = selected["id"]
    return {"die": value, "attack": selected}


def broken(state, aid, rng, definition):
    a = actor(state, aid)
    if definition.get("early_stage") or state["options"]["aliens_mode"]:
        a["dead"] = True
        return {"type": "creature_destroyed", "actor": aid}
    die = rng.randint(1, 6)
    if die == 1:
        a["rise_next_turn"] = True
    elif die == 2:
        a["speed"] = max(1, a["speed"] - 1)
        a["health"] = 1
        a["escape_check"] = True
    elif die == 3:
        a["health"] = 1
        state["pending"] = {"type": "creature_escape", "actor": aid, "zones": 2}
    elif die == 4:
        a["last_breath"] = True
    else:
        a["dead"] = True
    return {"type": "broken_creature", "actor": aid, "die": die}


def act(state, intent, bundle, rng):
    aid = intent.get("actor")
    a = actor(state, aid)
    combat = state["combat"]
    if not combat or aid not in combat["slots"][combat["index"]]["actors"] or a["species"] != "xenomorph" or a["dead"]:
        raise LoreKitError("Creature must be the current living combat actor")
    token = f"{combat['round']}:{combat['index']}"
    if a.get("acted_slot") == token:
        raise LoreKitError("Creature has already acted in this initiative slot")
    if a.get("rise_next_turn"):
        a["health"] = 1
        a.pop("rise_next_turn")
    target_id = intent.get("target")
    actor(state, target_id)
    chosen = select(state, aid, intent.get("feasible"), bundle, rng)
    a["acted_slot"] = token
    execution = {
        "actor": aid,
        "target": target_id,
        "actions": chosen["attack"]["actions"],
        "index": 0,
        "hit": False,
        "damage": False,
        "die": chosen["die"],
        "choices": copy.deepcopy(intent.get("choices", {})),
    }
    state["pending"] = {"type": "signature", "execution": execution}
    return resume(state, bundle, rng)


def resume(state, bundle, rng):
    p = state["pending"]
    execution = p["execution"]
    a, target = actor(state, execution["actor"]), actor(state, execution["target"])
    events = []
    while execution["index"] < len(execution["actions"]):
        action = execution["actions"][execution["index"]]
        execution["index"] += 1
        condition = action.get("when", "always")
        if condition == "hit" and not execution["hit"] or condition == "damage" and not execution["damage"]:
            continue
        hook = action["hook"]
        if hook == "stress":
            effects.stress(target, integer(action.get("value", 1), "signature stress"))
        elif hook == "panic":
            recipients = (
                effects.friendly(state, execution["target"])
                if action.get("scope") == "friends"
                else [execution["target"]]
            )
            for recipient in recipients:
                effects.enqueue_panic(state, recipient, "signature_attack")
        elif hook == "grapple":
            target["grappled_by"] = execution["actor"]
            a["grappling"] = execution["target"]
        elif hook == "instant_kill":
            if target["species"] == "human":
                target["health"] = 0
                target["dead"] = True
        elif hook == "facehug":
            if target["species"] != "human":
                raise LoreKitError("Facehug requires a living biological host")
            target["health"] = 0
            target["unconscious"] = True
            target["facehug"] = {
                "source": execution["actor"],
                "stage": "attached",
                "shift_due": state["clocks"]["shift"] + 1,
                "inseminated": False,
            }
            effects.stop_panic(target)
        elif hook == "drop_item":
            chosen = execution["choices"].get("item")
            from lorekit.alien.survival import item

            if chosen is None:
                execution["index"] -= 1
                state["pending"] = {"type": "signature_choice", "execution": execution, "choice": "item"}
                return {"type": "signature_choice", "events": events, "pending": copy.deepcopy(state["pending"])}
            entry, _ = item(target, chosen, bundle)
            entry.update(dropped=True, equipped=False, ground_zone=target["zone"])
        elif hook == "move":
            path = execution["choices"].get("path")
            if path is None:
                execution["index"] -= 1
                state["pending"] = {"type": "signature_choice", "execution": execution, "choice": "path"}
                return {"type": "signature_choice", "events": events, "pending": copy.deepcopy(state["pending"])}
            moving = execution["actor"] if action.get("scope", "self") == "self" else execution["target"]
            if not isinstance(path, list) or len(path) > action.get("zones", 1):
                raise LoreKitError("Signature movement path is too long")
            for destination in path:
                events.append(scenes.move(state, moving, destination, bundle, forced=True))
        elif hook == "attack":
            dice = integer(action.get("dice"), "signature dice", 1)
            damage = integer(action.get("damage", 1), "signature base damage")
            if scenes.range_band(state, execution["actor"], execution["target"]) > action.get("range", 0):
                if scenes.range_band(state, execution["actor"], execution["target"]) == 1:
                    # Creature signatures include a free Short-to-Adjacent move.
                    a.setdefault("adjacent", []).append(execution["target"])
                    target.setdefault("adjacent", []).append(execution["actor"])
                else:
                    raise LoreKitError("Signature target out of range")
            attack = {
                "actor": execution["actor"],
                "target": execution["target"],
                "weapon": None,
                "definition": {"damage": damage, "piercing": action.get("piercing", False)},
                "skill": "close_combat",
                "modifier": 0,
                "special": None,
                "weak_spot": False,
                "conserve": False,
                "full_auto": False,
                "burst": 1,
                "aim": False,
                "band": 0,
                "fixed_dice": dice,
                "signature_execution": copy.deepcopy(execution),
            }
            state["pending"] = {
                "type": "defense",
                "id": f"defense:{state['revision'] + 1}:{execution['index']}",
                "attack": attack,
            }
            return {
                "type": "defense_required",
                "events": events,
                "decision_id": state["pending"]["id"],
                "target": execution["target"],
            }
        else:
            raise LoreKitError("Unsupported signature hook")
    state["pending"] = None
    if a.get("last_breath"):
        a["dead"] = True
    events.extend(effects.drain(state, rng, bundle=bundle))
    return {"type": "signature_complete", "actor": execution["actor"], "die": execution["die"], "events": events}


def decide(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "signature_choice" or p["execution"]["actor"] != intent.get("actor"):
        raise LoreKitError("No matching signature choice")
    execution = p["execution"]
    execution["choices"][p["choice"]] = copy.deepcopy(intent.get(p["choice"]))
    state["pending"] = {"type": "signature", "execution": execution}
    return resume(state, bundle, rng)
