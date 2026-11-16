"""Data-driven creature actors and signature selection, using local content."""

from __future__ import annotations

import copy

from lorekit.alien import effects, scenes
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError

ATTACK_HOOKS = {
    "attack",
    "panic",
    "stress",
    "drop_item",
    "move",
    "grapple",
    "facehug",
    "instant_kill",
    "next_attack",
    "branch",
    "resist_damage",
    "critical_injury",
    "knockdown",
    "immobilize",
    "drag",
    "escape",
}


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
        "side": "opposition",
        "content_id": cid,
        "stage": definition.get("stage", "adult"),
        "size": definition.get("size", "normal"),
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
    followup = a.get("next_signature", {})
    if followup.get("force"):
        if followup["force"] not in feasible:
            raise LoreKitError("Forced signature is infeasible; an explicit GM ruling is required")
        forced = followup["force"]
        a["last_attack"] = definition["attacks"][str(forced)]["id"]
        return {"die": forced, "attack": copy.deepcopy(definition["attacks"][str(forced)])}
    bonus = followup.get("bonus", 0)
    outcomes = [
        i
        for i in range(1, 7)
        if min(6, i + bonus) in feasible and definition["attacks"][str(min(6, i + bonus))]["id"] != a.get("last_attack")
    ]
    if not outcomes:
        raise LoreKitError("No feasible signature: choose movement or an explicit GM ruling")
    raw = rng.choice(outcomes)
    value = min(6, raw + bonus)
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
        state["queue"].append({"type": "creature_escape", "actor": aid, "zones": 2})
    elif die == 4:
        a["last_breath"] = True
    else:
        a["dead"] = True
    return {"type": "broken_creature", "actor": aid, "die": die}


def act(state, intent, bundle, rng, *, immediate=False):
    aid = intent.get("actor")
    a = actor(state, aid)
    combat = state["combat"]
    if (
        not combat
        or (not immediate and aid not in combat["slots"][combat["index"]]["actors"])
        or a["species"] != "xenomorph"
        or a["dead"]
        or a.get("frozen")
        or a.get("attached_to")
    ):
        raise LoreKitError("Creature must be the current living combat actor")
    if a.get("escape_required"):
        raise LoreKitError("Wounded creature must use its escape movement this turn")
    if a.get("grappled_by"):
        raise LoreKitError("Grappled creature must attempt escape")
    token = f"hatch:{state['revision']}" if immediate else f"{combat['round']}:{combat['index']}"
    if a.get("acted_slot") == token:
        raise LoreKitError("Creature has already acted in this initiative slot")
    if a.get("rise_next_turn"):
        a["health"] = 1
        a.pop("rise_next_turn")
    # A tail hold ends when its source takes another action.
    for victim in state["actors"].values():
        if victim.get("immobilized_by") == aid:
            victim.pop("immobilized_by")
    target_id = intent.get("target")
    target = actor(state, target_id)
    if target["dead"] or a.get("stage") == "facehugger" and (target["species"] != "human" or target.get("facehug")):
        raise LoreKitError("Choose a living target; Facehuggers require an uninfected biological host")
    if scenes.range_band(state, aid, target_id) > 1:
        raise LoreKitError("Move within Short range before a signature attack")
    followup = a.get("next_signature")
    if followup:
        if followup.get("requires_grapple") and actor(state, followup["target"]).get("grappled_by") != aid:
            a.pop("next_signature")
        elif target_id != followup["target"]:
            raise LoreKitError("Signature followup must use its recorded target")
    if (
        a.get("stage") == "facehugger"
        and target["species"] == "human"
        and not target["dead"]
        and scenes.range_band(state, aid, target_id) == 0
        and (
            target.get("unconscious")
            or target.get("immobilized_by")
            or target.get("grappled_by")
            or target.get("stunned")
        )
    ):
        chosen = {"die": 6, "attack": {"actions": [{"hook": "facehug"}]}}
    else:
        chosen = select(state, aid, intent.get("feasible"), bundle, rng)
    a.pop("next_signature", None)
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
    if a["dead"] or target["dead"]:
        state["pending"] = None
        if a.get("last_breath"):
            a["dead"] = True
        return {"type": "signature_aborted", "actor": execution["actor"], "reason": "actor_or_target_dead"}
    while execution["index"] < len(execution["actions"]):
        action = execution["actions"][execution["index"]]
        execution["index"] += 1
        condition = action.get("when", "always")
        if condition == "hit" and not execution["hit"] or condition == "damage" and not execution["damage"]:
            continue
        hook = action["hook"]
        if hook == "next_attack":
            a["next_signature"] = {
                "target": execution["target"],
                "bonus": action.get("bonus", 0),
                "force": action.get("force"),
                "requires_grapple": action.get("requires_grapple", False),
            }
        elif hook == "branch":
            die = rng.randint(1, 6)
            execution["actions"][execution["index"] : execution["index"]] = copy.deepcopy(action["branches"][str(die)])
            events.append({"type": "signature_branch", "die": die})
        elif hook == "resist_damage":
            from lorekit.alien import checks
            from lorekit.alien.engine import complete_check

            state["pending"] = None
            result = checks.start(
                state,
                {
                    "actor": execution["target"],
                    "skill": action.get("skill", "stamina"),
                    "continuation": {
                        "kind": "signature_resist",
                        "execution": copy.deepcopy(execution),
                        "amount": action["amount"],
                    },
                },
                bundle,
                rng,
                queued=True,
            )
            return complete_check(state, result, bundle, rng)
        elif hook == "critical_injury":
            from lorekit.alien import survival

            if target["species"] == "human":
                events.append(survival.injury(state, execution["target"], bundle, rng, forced=action["injury"]))
        elif hook == "knockdown":
            target["prone"] = True
        elif hook == "immobilize":
            target["prone"] = True
            target["immobilized_by"] = execution["actor"]
        elif hook == "stress":
            effects.stress(target, integer(action.get("value", 1), "signature stress"))
        elif hook == "panic":
            recipients = (
                effects.friendly(state, execution["target"])
                if action.get("scope") == "friends"
                else [execution["target"]]
            )
            for recipient in recipients:
                effects.enqueue_panic(state, recipient, "signature_attack")
            if state["queue"]:
                state["pending"] = None
                state["queue"].append({"type": "signature_resume", "execution": copy.deepcopy(execution)})
                events.extend(effects.drain(state, rng, bundle=bundle))
                return {"type": "signature_effects", "actor": execution["actor"], "events": events}
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
            if target.get("facehug_proof"):
                events.append({"type": "facehug_blocked", "actor": execution["target"]})
                continue
            a["attached_to"] = execution["target"]
            before_health = target["health"]
            target["health"] = 0
            effects.broken_witnesses(state, execution["target"], before_health)
            target["unconscious"] = True
            target["facehug"] = {
                "source": execution["actor"],
                "stage": "attached",
                "shift_due": state["clocks"]["shift"] + 1,
                "inseminated": False,
            }
            effects.stop_panic(target)
        elif hook == "drop_item":
            if not any(not i.get("dropped") and i.get("quantity", 0) > 0 for i in target["inventory"]):
                events.append({"type": "nothing_to_drop", "actor": execution["target"]})
                continue
            chosen = execution["choices"].get("item")
            from lorekit.alien.survival import item

            if chosen is None:
                execution["index"] -= 1
                state["pending"] = {"type": "signature_choice", "execution": execution, "choice": "item"}
                return {"type": "signature_choice", "events": events, "pending": copy.deepcopy(state["pending"])}
            entry, _ = item(target, chosen, bundle)
            entry.update(dropped=True, equipped=False, ground_zone=target["zone"])
        elif hook in ("move", "drag", "escape"):
            path = execution["choices"].get("path")
            if path is None:
                execution["index"] -= 1
                state["pending"] = {"type": "signature_choice", "execution": execution, "choice": "path"}
                return {"type": "signature_choice", "events": events, "pending": copy.deepcopy(state["pending"])}
            moving = execution["actor"] if action.get("scope", "self") == "self" else execution["target"]
            if not isinstance(path, list) or not path or len(path) > action.get("zones", 1):
                raise LoreKitError("Signature movement path is too long")
            for destination in path:
                events.append(scenes.move(state, moving, destination, bundle, forced=True, rng=rng))
                if hook == "drag":
                    events.append(scenes.move(state, execution["target"], destination, bundle, forced=True, rng=rng))
            if hook == "drag":
                target["prone"] = True
            if hook == "escape":
                visible = any(
                    other["kind"] == "pc" and not other["dead"] and scenes.line_of_sight(state, tid, execution["actor"])
                    for tid, other in state["actors"].items()
                )
                if not visible:
                    a.update(hidden=True, revealed=False, escaped=True)
                events.append({"type": "signature_escape", "actor": execution["actor"], "escaped": not visible})
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
                "definition": {
                    "damage": damage,
                    "piercing": action.get("piercing", False),
                    "fixed_damage": action.get("fixed_damage", damage == 0),
                },
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


def release_egg(state, egg_id, host_id, bundle, rng):
    egg = actor(state, egg_id)
    definition = bundle["collections"]["creatures"][egg["content_id"]]
    if egg.get("stage") != "egg":
        raise LoreKitError("Actor is not an egg")
    if egg.get("opened") or egg["dead"] or egg.get("dormant"):
        return {"type": "egg_inactive", "actor": egg_id}
    host = actor(state, host_id)
    if host["species"] != "human" or host["dead"] or host["zone"] != egg["zone"]:
        raise LoreKitError("Egg release requires a living biological host within Short range")
    child_id = egg_id + "-hatchling"
    if child_id in state["actors"]:
        raise LoreKitError("Egg hatchling ID already exists")
    child = build(definition["spawn"], "Hatchling", bundle)
    child["character_id"] = None
    child["zone"] = egg["zone"]
    state["actors"][child_id] = child
    egg["opened"] = True
    if state["combat"]:
        state["combat"]["actors"].append(child_id)
        state["combat"]["units"].append([child_id])
        state["combat"]["budgets"][child_id] = {"full": False, "quick": 0}
    else:
        scenes.start_combat(state, {"actors": [host_id, child_id]}, rng, bundle)
    state["queue"].append({"type": "hatch_attack", "actor": child_id, "target": host_id})
    return {"type": "egg_opened", "actor": egg_id, "hatchling": child_id}


def egg_round(state, bundle, rng):
    events = []
    for aid, egg in list(state["actors"].items()):
        if egg.get("stage") != "egg" or egg["dead"] or egg.get("opened") or egg.get("dormant"):
            continue
        hosts = [
            hid
            for hid, host in sorted(state["actors"].items())
            if host["species"] == "human" and not host["dead"] and host["zone"] == egg["zone"]
        ]
        if hosts:
            die = rng.randint(1, 6)
            events.append({"type": "egg_release_roll", "actor": aid, "die": die})
            if die == 1:
                events.append(release_egg(state, aid, hosts[0], bundle, rng))
    return events


def infection_event(state, event, bundle, rng):
    a = actor(state, event["actor"])
    hug = a.get("facehug")
    if not hug or a["dead"]:
        return {"type": "infection_inactive", "actor": event["actor"]}
    if event["kind"] == "exposure":
        die = rng.randint(1, 6)
        if die == 1:
            hug["inseminated"] = True
        return {"type": "insemination_check", "actor": event["actor"], "die": die, "inseminated": hug["inseminated"]}
    source = actor(state, hug["source"])
    definition = bundle["collections"]["creatures"][source["content_id"]]
    if hug["stage"] == "attached":
        source["dead"] = True
        source.pop("attached_to", None)
        source["health"] = 0
        a["unconscious"] = False
        a["health"] = max(1, a["health"])
        hug["stage"] = "incubating"
        hug["inseminated"] = True
        hug["shift_due"] = state["clocks"]["shift"] + 1
        return {"type": "facehugger_detached", "actor": event["actor"]}
    template = definition.get("birth")
    if template is None:
        raise LoreKitError("Missing local Chestburster template for birth")
    child_id = event["actor"] + "-burster"
    if child_id in state["actors"]:
        raise LoreKitError("Birth actor ID already exists")
    child = build(template, "Newborn", bundle)
    child["character_id"] = None
    child["zone"] = a["zone"]
    child["mature_due"] = state["clocks"]["shift"] + 1
    state["actors"][child_id] = child
    a["dead"] = True
    a["health"] = 0
    a.pop("facehug")
    if state["combat"]:
        state["combat"]["actors"].append(child_id)
        state["combat"]["units"].append([child_id])
        state["combat"]["budgets"][child_id] = {"full": False, "quick": 0}
    for aid, witness in sorted(state["actors"].items()):
        if effects.eligible(witness) and witness["zone"] == a["zone"]:
            effects.enqueue_panic(state, aid, "witnessed_chestburster_birth")
    return {"type": "birth", "host": event["actor"], "creature": child_id}


def mature(state, aid, bundle):
    a = actor(state, aid)
    definition = bundle["collections"]["creatures"][a["content_id"]]
    if not definition.get("matures"):
        raise LoreKitError("Missing local adult template for maturation")
    updated = build(definition["matures"], "Mature creature", bundle)
    for key in (
        "character_id",
        "zone",
        "revealed",
        "hidden",
        "adjacent",
        "side",
        "grappled_by",
        "grappling",
        "acid",
        "burning",
        "rads",
        "hotspot",
        "radiation_level",
        "radiation_unit",
        "radiation_due",
        "hazards",
    ):
        if key in a:
            updated[key] = copy.deepcopy(a[key])
    state["actors"][aid] = updated
    return {"type": "matured", "actor": aid, "template": definition["matures"]}


def egg_adjacent(state, aid):
    """Schedule action-free disturbance checks once per host turn/approach."""
    host = actor(state, aid)
    if host["species"] != "human" or host["dead"]:
        return
    for egg_id in sorted(host.get("adjacent", [])):
        egg = actor(state, egg_id)
        if egg.get("stage") == "egg" and not (egg["dead"] or egg.get("opened") or egg.get("dormant")):
            state["queue"].append({"type": "egg_disturbance", "actor": aid, "egg": egg_id})


def remove_facehugger(state, intent, bundle, rng):
    from lorekit.alien import checks, survival

    aid, tid = intent.get("actor"), intent.get("target")
    actor(state, aid)
    host = actor(state, tid)
    hug = host.get("facehug")
    if not hug or hug["stage"] != "attached" or host["dead"]:
        raise LoreKitError("No attached Facehugger on this living host")
    if scenes.range_band(state, aid, tid) != 0:
        raise LoreKitError("Removal requires Adjacent range")
    if intent.get("extreme_cold") is not True:
        host.update(dead=True, health=0)
        actor(state, hug["source"]).pop("attached_to", None)
        host.pop("facehug")
        return {"type": "fatal_facehugger_removal", "actor": tid}
    if (
        intent.get("stretch_completed") is not True
        or not isinstance(intent.get("equipment"), str)
        or not intent["equipment"].strip()
    ):
        raise LoreKitError("Freezing removal requires extreme-cold equipment and one completed stretch")
    # Exposure during the procedure is resolved before its machinery check.
    survival.advance_time(state, "stretch", 1)
    events = effects.drain(state, rng, bundle=bundle)
    if state["pending"]:
        raise LoreKitError("Resolve due decisions before attempting removal")
    result = checks.start(
        state,
        {"actor": aid, "skill": "heavy_machinery", "continuation": {"kind": "facehugger_removal", "target": tid}},
        bundle,
        rng,
    )
    result["events"] = events + result.get("events", [])
    return result


def finish_removal(state, result):
    host = actor(state, result["continuation"]["target"])
    hug = host["facehug"]
    if result["success"]:
        source = actor(state, hug["source"])
        source["frozen"] = True
        source.pop("attached_to", None)
        source.pop("grappling", None)
        host["unconscious"] = False
        host["health"] = max(1, host["health"])
        if hug["inseminated"]:
            hug["stage"] = "incubating"
        else:
            host.pop("facehug")
    return {
        "type": "facehugger_removal",
        "success": result["success"],
        "infected": hug["inseminated"],
        "checks": result["checks"],
    }


def move_path(state, intent, bundle, rng):
    aid = intent.get("actor")
    a = actor(state, aid)
    if a["species"] != "xenomorph" or a.get("speed", 0) < 1 or a.get("frozen") or a.get("attached_to"):
        raise LoreKitError("Multi-zone combat movement requires an active mobile Xenomorph")
    path = intent.get("path")
    approach = intent.get("approach")
    if not isinstance(path, list) or not path or len(path) + int(approach is not None) > 2:
        raise LoreKitError("Creature movement uses up to two zones, or one zone and an approach")
    events = []
    for index, zone in enumerate(path):
        events.append(scenes.move(state, aid, zone, bundle, forced=index > 0, rng=rng))
    if approach is not None:
        target = actor(state, approach)
        if target["zone"] != a["zone"]:
            raise LoreKitError("Approach target must be in the arrival zone")
        a.setdefault("adjacent", []).append(approach)
        target.setdefault("adjacent", []).append(aid)
    return {"type": "creature_moved", "actor": aid, "events": events}


def escape_decide(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "creature_escape" or p["actor"] != intent.get("actor"):
        raise LoreKitError("No matching creature escape decision")
    path = intent.get("path")
    if not isinstance(path, list) or not path or len(path) > 2:
        raise LoreKitError("Choose a one/two-zone escape path")
    if p.get("turn_cost"):
        scenes.spend(state, p["actor"], "quick")
    events = [scenes.move(state, p["actor"], zone, bundle, forced=True, rng=rng) for zone in path]
    a = actor(state, p["actor"])
    visible = any(
        other["kind"] == "pc" and not other["dead"] and scenes.line_of_sight(state, tid, p["actor"])
        for tid, other in state["actors"].items()
    )
    if not visible and intent.get("route_unclear") is True:
        a.update(hidden=True, revealed=False, escaped=True)
    state["pending"] = None
    return {
        "type": "creature_escape",
        "actor": p["actor"],
        "escaped": a.get("escaped", False),
        "events": events + effects.drain(state, rng, bundle=bundle),
    }


def lifecycle(state, intent, bundle, rng):
    aid = intent.get("actor")
    a = actor(state, aid)
    reason = intent.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise LoreKitError("Life-cycle pacing requires an explicit GM reason")
    kind = intent.get("kind")
    if kind == "cold":
        if (
            a.get("stage") not in ("egg", "facehugger")
            or type(intent.get("active")) is not bool
            or a.get("attached_to")
        ):
            raise LoreKitError(
                "Cold dormancy/thawing supports eggs and unattached Facehuggers; use the removal procedure for an attached parasite"
            )
        a["dormant" if a["stage"] == "egg" else "frozen"] = intent["active"]
        return {"type": "creature_cold", "actor": aid, "active": intent["active"], "reason": reason}
    if kind == "stasis":
        active = intent.get("active")
        if type(active) is not bool or not a.get("facehug"):
            raise LoreKitError("Infection stasis requires an infected host and active:true/false")
        if active and not a.get("infection_stasis"):
            a["infection_stasis"] = {
                "remaining": max(0, a["facehug"]["shift_due"] - state["clocks"]["shift"]),
                "unconscious": a.get("unconscious", False),
            }
            a["unconscious"] = True
        elif not active and a.get("infection_stasis"):
            paused = a.pop("infection_stasis")
            a["facehug"]["shift_due"] = state["clocks"]["shift"] + paused["remaining"]
            a["unconscious"] = paused["unconscious"]
        return {"type": "infection_stasis", "actor": aid, "active": active, "reason": reason}
    field = "shift_due" if a.get("facehug") else "mature_due" if "mature_due" in a else None
    if field is None or a["dead"] or a.get("infection_stasis"):
        raise LoreKitError("No active life-cycle clock on this actor")
    owner = a["facehug"] if field == "shift_due" else a
    if kind == "deadline":
        owner[field] = integer(intent.get("shift"), "life-cycle due shift", state["clocks"]["shift"])
        return {"type": "lifecycle_deadline", "actor": aid, "shift": owner[field], "reason": reason}
    if kind == "resolve":
        result = (
            infection_event(state, {"actor": aid, "kind": "shift"}, bundle, rng)
            if field == "shift_due"
            else mature(state, aid, bundle)
        )
        result["reason"] = reason
        result["events"] = result.get("events", []) + effects.drain(state, rng, bundle=bundle)
        return result
    raise LoreKitError("Choose deadline, resolve or infection stasis")
