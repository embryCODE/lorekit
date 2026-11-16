"""Explosive weapon defenses are declared before the shared attack roll."""

from __future__ import annotations

import copy

from lorekit.alien import checks, combat, effects, scenes
from lorekit.alien.state import actor
from lorekit.db import LoreKitError


def begin(state, spec, attack):
    targets = [
        tid
        for tid, target in sorted(state["actors"].items())
        if tid != attack["target"] and target["zone"] == actor(state, attack["target"])["zone"] and not target["dead"]
    ]
    state["pending"] = {
        "type": "area_defenses",
        "spec": spec,
        "attack": attack,
        "targets": targets,
        "choices": [],
        "index": 0,
    }
    return {"type": "area_defenses", "pending": copy.deepcopy(state["pending"])}


def decide(state, intent, bundle, rng):
    p = state["pending"]
    if not p or p["type"] != "area_defenses":
        raise LoreKitError("No explosive-weapon defense decision")
    if p["index"] < len(p["targets"]):
        tid = p["targets"][p["index"]]
        if intent.get("actor") != tid or intent.get("choice") not in ("dodge", "decline"):
            raise LoreKitError("Next secondary target must choose dodge or decline")
        target = actor(state, tid)
        if intent["choice"] == "dodge":
            if target["species"] == "xenomorph" or target["health"] == 0 or target.get("cover") == "full":
                raise LoreKitError("This target cannot dodge")
            scenes.spend(state, tid, "quick", out_of_turn=True)
        p["choices"].append(intent["choice"])
        p["index"] += 1
    if p["index"] < len(p["targets"]):
        return {"type": "area_defenses", "pending": copy.deepcopy(p)}
    spec = p["spec"]
    opponents = [spec.pop("opponent")] if "opponent" in spec else []
    quick_shot = spec["continuation"] if spec["continuation"]["kind"] == "quick_shot" else None
    primary_index = 1 if opponents and not quick_shot else None
    targets = []
    for tid, choice in zip(p["targets"], p["choices"], strict=True):
        index = None
        if choice == "dodge":
            opponents.append({"actor": tid, "skill": "mobility", "context": "dodge"})
            index = len(opponents)
        targets.append({"actor": tid, "index": index})
    spec["opponents"] = opponents
    spec["_area"] = True
    spec["continuation"] = {
        "kind": "area",
        "attack": p["attack"],
        "primary_index": primary_index,
        "targets": targets,
        "quick_shot": quick_shot,
    }
    state["pending"] = None
    return checks.start(state, spec, bundle, rng, queued=True)


def finish(state, result, bundle, rng):
    c = result["continuation"]
    if c.get("quick_shot"):
        area_result = copy.deepcopy(result)
        area_result["continuation"]["quick_shot"] = None
        return combat.finish_quick_shot(
            state,
            {
                "checks": result["checks"][:2],
                "successes": result["successes"][:2],
                "continuation": {**c["quick_shot"], "area_result": area_result},
            },
            bundle,
            rng,
        )
    primary = copy.deepcopy(c["attack"])
    scores = result["successes"]

    def outcome(attack, index):
        return {
            "type": "check_complete",
            "successes": [scores[0]] + ([scores[index]] if index else []),
            "checks": [result["checks"][0]] + ([result["checks"][index]] if index else []),
            "continuation": {"kind": "attack", "attack": attack},
        }

    net = scores[0] - (scores[c["primary_index"]] if c["primary_index"] else 0)
    secondary = []
    if net > 0:
        for entry in c["targets"]:
            attack = copy.deepcopy(primary)
            attack.update(target=entry["actor"], weapon=None, full_auto=False, conserve=False, area_secondary=True)
            attack["definition"]["damage"] = primary["definition"]["damage"]
            secondary.append(
                {
                    "type": "shot_followup",
                    "actor": primary["actor"],
                    "simultaneous": True,
                    "result": outcome(attack, entry["index"]),
                }
            )
    primary["defer_burst"] = True
    if primary["full_auto"] and net > 0 and result["checks"][0]["pushes"] == 0 and primary["burst"] < 3:
        secondary.append({"type": "burst_offer", "attack": primary})
    state["queue"][0:0] = secondary
    return combat.finish(state, outcome(primary, c["primary_index"]), bundle, rng)
