"""Explicit talent use: finite uses and GM answers remain durable decisions."""

from __future__ import annotations

import copy

from lorekit.alien import characters, checks, effects, scenes, survival
from lorekit.alien.state import actor, integer
from lorekit.db import LoreKitError

QUESTIONS = {"origin", "alive", "age", "purpose", "operation", "risks"}


def act(state, intent, bundle, rng):
    aid = intent.get("actor")
    a = actor(state, aid)
    hook = intent.get("talent")
    if not any(e["hook"] == hook for e in characters.talents(a, bundle)):
        raise LoreKitError("Actor lacks this implemented talent")
    if characters.incapacitated(a):
        raise LoreKitError("Actor cannot use this talent while incapacitated")
    if hook == "company_resources":
        act_id = intent.get("act_id")
        if not isinstance(act_id, str) or not act_id.strip():
            raise LoreKitError("Company Resources requires the cinematic act ID")
        if act_id in a.get("company_uses", []):
            raise LoreKitError("Company Resources already used this act")
        item_id, instance_id = intent.get("content_id"), intent.get("item")
        definition = bundle["collections"].get("items", {}).get(item_id)
        if not definition or definition.get("weight", 1) > 1:
            raise LoreKitError("Choose a supported regular-sized item")
        if not isinstance(instance_id, str) or not instance_id or any(i["id"] == instance_id for i in a["inventory"]):
            raise LoreKitError("A new inventory instance ID is required")
        a.setdefault("company_uses", []).append(act_id)
        a["inventory"].append(
            {
                "id": instance_id,
                "content_id": item_id,
                "quantity": 1,
                "supply": definition.get("capacity", 0),
                "reloads": 0,
                "equipped": False,
                "company_owned": True,
            }
        )
        effects.stress(a, 1)
        return {"type": "company_resources", "actor": aid, "item": instance_id, "company_owned": True}
    if hook == "nurse":
        ids = intent.get("patients")
        capacity = a["skills"]["medical_aid"]
        if (
            state["mode"] == "combat"
            or intent.get("shift_completed") is not True
            or not isinstance(ids, list)
            or not ids
            or len(ids) > capacity
            or len(set(ids)) != len(ids)
        ):
            raise LoreKitError("Nursing needs a completed shift and distinct patients within Medical Aid capacity")
        if a.get("care_shift_used") == state["clocks"]["shift"]:
            raise LoreKitError("This completed shift has already been spent on care")
        a["care_shift_used"] = state["clocks"]["shift"]
        for tid in ids:
            patient = actor(state, tid)
            if patient["dead"] or patient["zone"] != a["zone"]:
                raise LoreKitError("Nursing requires living patients in the same zone")
            patient["nursed_until_day"] = state["clocks"]["day"] + 1
        return {"type": "nursing", "actor": aid, "patients": ids, "day": state["clocks"]["day"] + 1}
    if hook not in {"analysis", "xenomorphology", "pull_rank", "counselor"}:
        raise LoreKitError("This talent does not have an explicit use action")
    continuation = {"kind": "talent", "talent": hook, "actor": aid}
    if hook == "analysis":
        object_id = intent.get("object")
        if (
            not isinstance(object_id, str)
            or not object_id.strip()
            or intent.get("stretch_completed") is not True
            or state["mode"] == "combat"
        ):
            raise LoreKitError("Analysis needs an examined object ID and a completed stretch outside combat")
        if object_id in a.get("analyzed", []):
            raise LoreKitError("This object has already been analyzed")
        if any(object_id in other.get("analyzed", []) for other in state["actors"].values()):
            raise LoreKitError("This object has already had its single Analysis attempt")
        a.setdefault("analyzed", []).append(object_id)
        continuation["object"] = object_id
        skill = "observation"
    else:
        tid = intent.get("target")
        target = actor(state, tid)
        continuation["target"] = tid
        if hook == "xenomorphology":
            if target["species"] != "xenomorph" or not scenes.line_of_sight(state, aid, tid):
                raise LoreKitError("Examine a visible extraterrestrial creature")
            if tid in a.get("xenomorphology_targets", []):
                raise LoreKitError("This creature has already been examined")
            a.setdefault("xenomorphology_targets", []).append(tid)
            scenes.spend(state, aid, "quick")
            skill = "observation"
        elif hook == "pull_rank":
            if (
                target["kind"] != "npc"
                or intent.get("same_organization") is not True
                or intent.get("non_officer") is not True
                or not isinstance(intent.get("order"), str)
                or not intent["order"].strip()
            ):
                raise LoreKitError("Pull Rank requires a non-officer NPC in your organization and an order")
            if target["zone"] != a["zone"] and intent.get("comms") is not True:
                raise LoreKitError("Order requires speaking range or comms")
            effects.stress(a, 1)
            continuation["order"] = intent["order"]
            scenes.spend(state, aid, "full")
            skill = "command"
        else:
            if tid == aid or target["kind"] != "pc" or target["zone"] != a["zone"] or target["dead"]:
                raise LoreKitError("Counselor requires another living PC in the same zone")
            if a.get("counselor_stretch") == state["clocks"]["stretch"]:
                raise LoreKitError("Counselor already used this stretch")
            a["counselor_stretch"] = state["clocks"]["stretch"]
            skill = "manipulation"
            scenes.spend(state, aid, "full")
    return checks.start(state, {"actor": aid, "skill": skill, "continuation": continuation}, bundle, rng)


def finish(state, result):
    c = result["continuation"]
    hook, aid = c["talent"], c["actor"]
    a = actor(state, aid)
    successes = result["successes"][0]
    event = {
        "type": "talent_result",
        "talent": hook,
        "actor": aid,
        "checks": result["checks"],
        "success": result["success"],
    }
    if hook == "analysis":
        for tid, other in state["actors"].items():
            if tid != aid and other["kind"] == "pc" and other["zone"] == a["zone"] and not other["dead"]:
                if result["success"]:
                    from lorekit.alien.consumables import relieve

                    relieve(state, tid, 1)
                else:
                    effects.stress(other, 1)
        if result["success"]:
            state["pending"] = {"type": "talent_answer", **c, "remaining": successes, "asked": []}
    elif hook == "xenomorphology":
        if result["success"]:
            state["pending"] = {"type": "talent_answer", **c, "remaining": 1, "asked": []}
        else:
            effects.stress(a, 1)
    elif hook == "pull_rank" and result["success"]:
        actor(state, c["target"])["rank_order"] = c["order"]
        event["order"] = c["order"]
    elif hook == "counselor" and result["success"]:
        target = actor(state, c["target"])
        from lorekit.alien.consumables import relieve

        relieve(state, c["target"], successes)
    event["pending"] = copy.deepcopy(state["pending"])
    return event


def answer(state, intent):
    p = state["pending"]
    if not p or p["type"] != "talent_answer" or intent.get("actor") != p["actor"]:
        raise LoreKitError("No matching talent answer decision")
    question, answer = intent.get("question"), intent.get("answer")
    valid = QUESTIONS if p["talent"] == "analysis" else {"weakness"}
    if question not in valid or question in p["asked"] or not isinstance(answer, str) or not answer.strip():
        raise LoreKitError("Choose a remaining question and record the GM's answer")
    p["asked"].append(question)
    p["remaining"] -= 1
    if not p["remaining"] or len(p["asked"]) == len(valid):
        state["pending"] = None
    return {"type": "talent_answer", "question": question, "answer": answer, "pending": copy.deepcopy(state["pending"])}
