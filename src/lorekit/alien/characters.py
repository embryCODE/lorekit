"""Validated Alien actors; source-specific character options come from local content."""

from __future__ import annotations

import copy
import math

from lorekit.alien.state import integer
from lorekit.db import LoreKitError

ATTRIBUTES = ("strength", "agility", "wits", "empathy")
SKILLS = dict(
    zip(
        (
            "heavy_machinery",
            "stamina",
            "close_combat",
            "mobility",
            "piloting",
            "ranged_combat",
            "comtech",
            "observation",
            "survival",
            "command",
            "manipulation",
            "medical_aid",
        ),
        ("strength",) * 3 + ("agility",) * 3 + ("wits",) * 3 + ("empathy",) * 3,
        strict=True,
    )
)


def talents(actor: dict, bundle: dict) -> list[dict]:
    entries = bundle["collections"].get("talents", {})
    result = []
    for tid in actor["talents"]:
        if tid not in entries:
            raise LoreKitError("Missing actor talent in local bundle")
        result.extend(entries[tid].get("effects", []))
    return result


def bonus(actor: dict, bundle: dict, hook: str) -> int:
    return sum(e.get("value", 0) for e in talents(actor, bundle) if e["hook"] == hook)


def build(spec: dict, bundle: dict, *, imported: bool = False) -> dict:
    if not isinstance(spec, dict):
        raise LoreKitError("Character build must be an object")
    allowed = {
        "name",
        "species",
        "kind",
        "attributes",
        "skills",
        "career",
        "talents",
        "synthetic_bonuses",
        "inventory",
        "private",
        "secret_identity",
        "revealed",
        "health",
        "stress",
        "story_points",
        "armor",
    }
    if set(spec) - allowed:
        raise LoreKitError("Unknown character build fields")
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        raise LoreKitError("Character name is required")
    species = spec.get("species", "human")
    kind = spec.get("kind", "pc")
    if species not in ("human", "synthetic") or kind not in ("pc", "npc"):
        raise LoreKitError("Unsupported actor species or kind")
    attrs = spec.get("attributes", {})
    if not isinstance(attrs, dict) or set(attrs) != set(ATTRIBUTES):
        raise LoreKitError("Exactly four attributes are required")
    attrs = {key: integer(value, key, 1, 8 if species == "synthetic" else 5) for key, value in attrs.items()}
    skills = spec.get("skills", {})
    if not isinstance(skills, dict) or set(skills) - set(SKILLS):
        raise LoreKitError("Unknown skill")
    skills = {key: integer(skills.get(key, 0), key, 0, 5) for key in SKILLS}
    talent_ids = spec.get("talents", [])
    if not isinstance(talent_ids, list) or any(not isinstance(t, str) for t in talent_ids):
        raise LoreKitError("talents must be a list of content IDs")
    talent_entries = bundle["collections"].get("talents", {})
    for tid in set(talent_ids):
        if tid not in talent_entries:
            raise LoreKitError("Unsupported or missing talent")
        integer(talent_entries[tid].get("max_selections", 1), "max_selections", 1, 3)
        if talent_ids.count(tid) > talent_entries[tid].get("max_selections", 1):
            raise LoreKitError("Talent selection limit exceeded")
    if not imported:
        career = bundle["collections"].get("careers", {}).get(spec.get("career"))
        if career is None:
            raise LoreKitError("Standard creation requires a local career entry")
        if sum(attrs.values()) != 14 or any(
            v < 2 or v > (5 if k == career["attribute"] else 4) for k, v in attrs.items()
        ):
            raise LoreKitError("Invalid 14-point standard attribute allocation")
        if sum(skills.values()) != 10 or any(v > (3 if k in career["skills"] else 1) for k, v in skills.items()):
            raise LoreKitError("Invalid 10-point standard skill allocation")
        if len(talent_ids) != 1 or talent_ids[0] not in career["talents"]:
            raise LoreKitError("Choose exactly one career talent")
        if species == "synthetic":
            selected = spec.get("synthetic_bonuses", [])
            if (
                not isinstance(selected, list)
                or len(selected) != 2
                or len(set(selected)) != 2
                or not set(selected) <= set(attrs)
            ):
                raise LoreKitError("Choose two distinct synthetic bonus attributes")
            for key in selected:
                attrs[key] += 3
        elif spec.get("synthetic_bonuses"):
            raise LoreKitError("Human cannot receive synthetic bonuses")
    elif spec.get("synthetic_bonuses"):
        raise LoreKitError("Imported attributes are final; do not add synthetic bonuses twice")
    a = {
        "name": spec["name"].strip(),
        "species": species,
        "kind": kind,
        "career": spec.get("career"),
        "attributes": attrs,
        "skills": skills,
        "talents": copy.deepcopy(talent_ids),
        "inventory": [],
        "effects": [],
        "injuries": [],
        "stress_responses": [],
        "panic_responses": [],
        "private": copy.deepcopy(spec.get("private", {})),
        "secret_identity": spec.get("secret_identity", False),
        "revealed": spec.get("revealed", kind == "pc"),
        "story_points": integer(spec.get("story_points", 0), "story_points", 0, 3),
        "armor": integer(spec.get("armor", 0), "armor", 0, 20),
        "zone": None,
        "hidden": False,
        "dead": False,
        "fatigued": False,
        "rads": 0,
    }
    for field in ("secret_identity", "revealed"):
        if type(a[field]) is not bool:
            raise LoreKitError(f"{field} must be boolean")
    if not isinstance(a["private"], dict):
        raise LoreKitError("private must be an object")
    a["health_max"] = math.ceil((attrs["strength"] + attrs["agility"]) / 2) + bonus(a, bundle, "health_bonus")
    a["resolve"] = math.ceil((attrs["wits"] + attrs["empathy"]) / 2) + bonus(a, bundle, "resolve_bonus")
    a["health"] = integer(spec.get("health", a["health_max"]), "health", 0, a["health_max"])
    a["stress"] = integer(spec.get("stress", 0), "stress")
    if (kind != "pc" or species != "human") and a["stress"]:
        raise LoreKitError("Only human PCs track stress")
    inventory = spec.get("inventory", [])
    if not isinstance(inventory, list):
        raise LoreKitError("inventory must be a list")
    for entry in inventory:
        if not isinstance(entry, dict) or set(entry) - {
            "id",
            "content_id",
            "quantity",
            "supply",
            "reloads",
            "equipped",
            "active_air",
        }:
            raise LoreKitError("Invalid inventory entry")
        item = bundle["collections"].get("items", {}).get(entry.get("content_id"))
        if item is None or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise LoreKitError("Inventory requires instance ID and supported item content ID")
        if any(i["id"] == entry["id"] for i in a["inventory"] if not i.get("dropped", False)):
            raise LoreKitError("Duplicate inventory instance ID")
        for flag in ("equipped", "active_air"):
            if flag in entry and type(entry[flag]) is not bool:
                raise LoreKitError("Inventory flags must be boolean")
        a["inventory"].append(
            {
                **copy.deepcopy(entry),
                "quantity": integer(entry.get("quantity", 1), "quantity", 1),
                "supply": integer(entry.get("supply", item.get("capacity", 0)), "supply", 0, item.get("capacity", 0)),
                "reloads": integer(entry.get("reloads", 0), "reloads"),
                "equipped": bool(entry.get("equipped", False)),
            }
        )
    refresh_equipment(a, bundle)
    return a


def refresh_equipment(a, bundle):
    armor = []
    protections = {"acid_proof": False, "facehug_proof": False, "vacuum_proof": False}
    for instance in a["inventory"]:
        if not instance.get("equipped") or instance.get("dropped"):
            continue
        definition = bundle["collections"].get("items", {})[instance["content_id"]]
        for key in protections:
            protections[key] |= definition.get(key, False)
        if "armor" in definition:
            armor.append((instance, definition))
    if len(armor) > 1:
        raise LoreKitError("Only one suit of armor can be worn")
    if armor:
        instance, definition = armor[0]
        instance.setdefault("armor_current", definition["armor"])
        integer(instance["armor_current"], "item armor", 0, definition["armor"])
        a["armor"] = instance["armor_current"]
        a["armor_item"] = instance["id"]
    elif a.get("armor_item"):
        a["armor"] = 0
        a.pop("armor_item")
    a.update(protections)


def load_weight(a: dict, bundle: dict) -> tuple[float, int]:
    entries = bundle["collections"].get("items", {})
    weight = sum(
        entries[i["content_id"]].get("weight", 1) * i["quantity"] + 0.25 * i.get("reloads", 0)
        for i in a["inventory"]
        if not i.get("dropped")
    )
    multipliers = [e["value"] for e in talents(a, bundle) if e["hook"] == "carry_multiplier"]
    limit = 2 * a["attributes"]["strength"] * (max(multipliers) if multipliers else 1)
    return weight, limit
